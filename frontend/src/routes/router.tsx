import { createBrowserRouter, Navigate } from "react-router";

import { AppLayout } from "@/components/layout/AppLayout";
import { ChatPage } from "@/pages/ChatPage";
import { EmptyPage } from "@/pages/EmptyPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppLayout />,
    children: [
      { index: true, element: <EmptyPage /> },
      { path: "s/:sid", element: <ChatPage /> },
      // 老链接兜底
      { path: "sessions/:sid", element: <Navigate to="../s/:sid" replace /> },
    ],
  },
]);
