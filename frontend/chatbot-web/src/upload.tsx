import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { UploadPage } from "./pages/UploadPage";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App>
      <UploadPage />
    </App>
  </StrictMode>,
);
