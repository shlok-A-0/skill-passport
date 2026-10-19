import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { loadSkillNames } from "./skillNames";
import "./styles.css";

// Non-blocking load: skill names cache updates in the background without delaying first paint
loadSkillNames();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
