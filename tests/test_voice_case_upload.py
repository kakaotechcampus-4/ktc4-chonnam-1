"""보이스피싱 녹음 업로드 API 테스트 (신규 시나리오).

실제 STT·AI 분석은 다음 주 연동 전까지 없다 — 업로드 → 형식·크기
검증 → 목업 처리 → 결과 조회까지 전체 배관이 끝까지 도는지, 그리고
잘못된 업로드를 올바르게 거부하는지 검증한다.

업로드는 메모리에 통째로 올리지 않고 청크 단위로 임시 파일에 쓴다
(윤여경 리뷰: 25MB 업로드가 동시에 여러 건 오면 메모리가 배로 는다) —
그래서 처리 경로는 bytes가 아니라 파일 경로를 주고받고, 성공·실패와
무관하게 그 파일을 반드시 지운다.
"""

import os

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture(autouse=True)
def clean_cases():
    main.VOICE_CASES.clear()
    yield
    main.VOICE_CASES.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


class TestUploadVoiceCase:
    def test_valid_upload_returns_202_with_case_id(self, client):
        response = client.post(
            "/api/voice-cases",
            files={"file": ("call.m4a", b"fake audio bytes", "audio/mp4")},
        )

        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "processing"
        assert body["case_id"]

    @pytest.mark.parametrize("extension", ["m4a", "mp3", "wav", "webm"])
    def test_all_supported_extensions_are_accepted(self, client, extension):
        response = client.post(
            "/api/voice-cases",
            files={"file": (f"call.{extension}", b"fake audio bytes", "application/octet-stream")},
        )

        assert response.status_code == 202

    def test_unsupported_extension_is_rejected(self, client):
        response = client.post(
            "/api/voice-cases",
            files={"file": ("call.exe", b"not audio", "application/octet-stream")},
        )

        assert response.status_code == 415
        assert main.VOICE_CASES == {}

    def test_empty_file_is_rejected(self, client):
        response = client.post(
            "/api/voice-cases",
            files={"file": ("call.m4a", b"", "audio/mp4")},
        )

        assert response.status_code == 400
        assert main.VOICE_CASES == {}

    def test_oversized_file_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(main, "MAX_VOICE_FILE_BYTES", 10)

        response = client.post(
            "/api/voice-cases",
            files={"file": ("call.m4a", b"this payload is longer than ten bytes", "audio/mp4")},
        )

        assert response.status_code == 413
        assert main.VOICE_CASES == {}


class TestVoiceCaseLifecycle:
    def test_uploaded_case_eventually_completes_with_mock_result(self, client):
        upload = client.post(
            "/api/voice-cases",
            files={"file": ("call.wav", b"fake audio bytes", "audio/wav")},
        )
        case_id = upload.json()["case_id"]

        response = client.get(f"/api/voice-cases/{case_id}")

        assert response.status_code == 200
        body = response.json()
        assert body["case_id"] == case_id
        assert body["status"] == "completed"
        assert body["result"]["mock"] is True
        assert body["result"]["scenario"] == "voice_phishing"

    def test_unknown_case_id_returns_404(self, client):
        response = client.get("/api/voice-cases/does-not-exist")

        assert response.status_code == 404

    async def test_process_failure_marks_case_failed(self, monkeypatch, tmp_path):
        async def failing_mock(audio_path, filename):
            raise RuntimeError("stt unavailable")

        monkeypatch.setattr(main, "mock_analyze_voice_case", failing_mock)

        audio_path = tmp_path / "call.m4a"
        audio_path.write_bytes(b"audio")
        case_id = main.create_voice_case()
        await main.process_voice_case(case_id, str(audio_path), "call.m4a")

        case = main.VOICE_CASES[case_id]
        assert case["status"] == "failed"
        assert case["error"] == "RuntimeError"
        assert case["result"] is None


class TestVoiceUploadMemoryHandling:
    """메모리에 전체를 올리지 않고 청크로 읽어 디스크에 바로 흘려보내는지,
    그리고 처리 뒤 임시 파일을 남기지 않는지 검증한다 (윤여경 리뷰)."""

    def test_upload_reads_in_fixed_size_chunks_not_all_at_once(self, client, monkeypatch):
        read_sizes = []
        original_save = main._save_voice_upload

        async def spying_save(file):
            original_read = file.read

            async def spying_read(size=-1):
                read_sizes.append(size)
                return await original_read(size)

            file.read = spying_read
            return await original_save(file)

        monkeypatch.setattr(main, "_save_voice_upload", spying_save)

        client.post(
            "/api/voice-cases",
            files={"file": ("call.m4a", b"x" * (2 * 1024 * 1024), "audio/mp4")},
        )

        # 전체 크기(-1, 즉 한 번에 다 읽기)가 아니라 정해진 청크 크기를
        # 매번 요청했는지 확인한다.
        assert read_sizes
        assert all(size == main._VOICE_UPLOAD_CHUNK_BYTES for size in read_sizes)
        assert len(read_sizes) >= 2  # 2MB 파일을 1MB 청크로 받았으니 최소 2번

    def test_temp_file_is_removed_after_successful_processing(self, client, monkeypatch):
        captured_paths = []
        original_process = main.process_voice_case

        async def spying_process(case_id, path, filename):
            captured_paths.append(path)
            await original_process(case_id, path, filename)

        monkeypatch.setattr(main, "process_voice_case", spying_process)

        client.post(
            "/api/voice-cases",
            files={"file": ("call.m4a", b"fake audio bytes", "audio/mp4")},
        )

        assert captured_paths
        assert not os.path.exists(captured_paths[0])

    async def test_temp_file_is_removed_even_when_processing_fails(self, monkeypatch, tmp_path):
        async def failing_mock(audio_path, filename):
            raise RuntimeError("stt unavailable")

        monkeypatch.setattr(main, "mock_analyze_voice_case", failing_mock)

        audio_path = tmp_path / "call.m4a"
        audio_path.write_bytes(b"audio")
        case_id = main.create_voice_case()

        await main.process_voice_case(case_id, str(audio_path), "call.m4a")

        assert not audio_path.exists()
