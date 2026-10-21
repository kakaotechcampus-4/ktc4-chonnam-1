import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { ResultPage } from "./pages/ResultPage";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App>
      <ResultPage />
    </App>
  </StrictMode>,
);
