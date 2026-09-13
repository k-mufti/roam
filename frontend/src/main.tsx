import React from "react";
import ReactDOM from "react-dom/client";
// Leaflet's CSS is imported from node_modules rather than a CDN so the app
// works offline and the version can never drift from the JS package.
import "leaflet/dist/leaflet.css";
import "./styles.css";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
