import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router";
import { Toaster } from "sonner";

import "./index.css";
import { router } from "./routes/router";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <RouterProvider router={router} />
    <Toaster
      position="bottom-right"
      toastOptions={{
        style: {
          background: "var(--color-bg-card)",
          border: "1px solid var(--color-line)",
          color: "var(--color-paper)",
          fontFamily: "var(--font-mono)",
          fontSize: "12px",
          borderRadius: "var(--radius)",
        },
      }}
    />
  </StrictMode>,
);
