// React entry point — mounts <App/> into #root and loads the base stylesheet.
//
// StrictMode is on in dev to surface accidental side-effect bugs early; it is a no-op in the production
// build. Nothing app-specific lives here — bootstrap only.

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <App />
  </StrictMode>
);
