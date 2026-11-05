/**
 * 聊天中心 API：对话列表 / 消息收发；图片与内联键盘按钮。
 */
import {
  MEDIUM_TIMEOUT_MS,
  request,
  requestBlob,
} from "./core";

export interface ChatButton {
  row: number;
  col: number;
  text: string;
  kind: "url" | "callback" | "reply";
  url?: string | null;
}

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
  media_type?: string | null;
  keyboard_type?: string | null;
  buttons?: ChatButton[];
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

/** 下载一条图片消息：thumb=true 缩略图，thumb=false 原图。 */
export const fetchChatMediaBlob = (
  token: string,
  accountName: string,
  chatId: number | string,
  messageId: number,
  thumb = true,
) =>
  requestBlob(
    `/chats/${encodeURIComponent(accountName)}/media/${encodeURIComponent(String(messageId))}?chat_id=${encodeURIComponent(String(chatId))}&thumb=${thumb ? "true" : "false"}`,
    {},
    token,
    MEDIUM_TIMEOUT_MS,
  );

/** 点一条消息的内联键盘按钮：链接按钮返回 url，回调按钮返回 Bot 回答。 */
export const pressChatButton = (
  token: string,
  accountName: string,
  chatId: number | string,
  messageId: number,
  row: number,
  col: number,
) =>
  request<{ action: "url" | "callback"; message: string; alert: boolean; url?: string | null }>(
    `/chats/${encodeURIComponent(accountName)}/button`,
    { method: "POST", body: JSON.stringify({ chat_id: chatId, message_id: messageId, row, col }) },
    token,
    MEDIUM_TIMEOUT_MS,
  );
