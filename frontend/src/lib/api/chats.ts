/**
 * 聊天中心 API：对话列表 / 消息收发（仅文本）。
 */
import {
  MEDIUM_TIMEOUT_MS,
  request,
} from "./core";

export interface ChatDialog {
  chat_id: number | string;
  title: string;
  type: string;
  username?: string | null;
  unread_count: number;
  last_message_text: string;
  last_message_date?: string | null;
}

export interface ChatMessage {
  id?: number | null;
  date?: string | null;
  text: string;
  outgoing: boolean;
  sender_name: string;
  sender_id?: number | null;
}

export const listChatDialogs = (token: string, accountName: string, limit = 50) =>
  request<{ dialogs: ChatDialog[]; total: number }>(
    `/chats/dialogs?account=${encodeURIComponent(accountName)}&limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export const listChatMessages = (
  token: string,
  accountName: string,
  chatId: number | string,
  limit = 50,
) =>
  request<{ messages: ChatMessage[]; total: number }>(
    `/chats/${encodeURIComponent(accountName)}/messages?chat_id=${encodeURIComponent(String(chatId))}&limit=${encodeURIComponent(String(limit))}`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

export const sendChatMessage = (
  token: string,
  accountName: string,
  chatId: number | string,
  text: string,
) =>
  request<{ success: boolean; message: ChatMessage }>(
    `/chats/${encodeURIComponent(accountName)}/messages`,
    { method: "POST", body: JSON.stringify({ chat_id: chatId, text }) },
    token,
    MEDIUM_TIMEOUT_MS,
  );
