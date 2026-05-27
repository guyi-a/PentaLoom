import { createBrowserRouter, Navigate } from "react-router";

import { AppLayout } from "@/components/layout/AppLayout";
import { ChatPage } from "@/pages/ChatPage";
import { EmptyPage } from "@/pages/EmptyPage";

// PentaLoom 单页结构:
//   /              — 引导态, 鼓励新建会话或挑一个旧的
//   /s/:sid        — 单个会话的聊天主区
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
