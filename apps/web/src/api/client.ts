import type { components } from './schema';
export type Account = components['schemas']['AccountView'];
export type ModelProfile = components['schemas']['ModelView'];
export type Schedule = components['schemas']['ScheduleInput'];
export type Task = components['schemas']['TaskView'];
export type Mail = components['schemas']['MailView'];
export type Digest = components['schemas']['DigestView'];
export type Conversation = components['schemas']['ConversationView'];
export type Message = components['schemas']['MessageView'];
export type DigestItem = {
  mail_id: string;
  priority: 'high' | 'medium' | 'low';
  summary: string;
  action: string;
  deadline: string | null;
  reason: string;
};
export type Source = {
  id: string;
  subject: string;
  sender: string;
  account_label: string;
  sent_at: string;
};
export type Auth = { initialized: boolean; authenticated: boolean; mode: 'live' | 'demo' };
export type Overview = {
  accounts: Account[];
  mail_count: number;
  latest_digest: Digest | null;
  tasks: Task[];
  schedule: Schedule;
  worker: { alive: boolean; heartbeat_at: string | null };
  model: ModelProfile;
  onboarding: { step: number; complete: boolean };
  mode: 'live' | 'demo';
};
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch('/api/v1' + path, {
    credentials: 'same-origin',
    ...init,
    headers: { 'Content-Type': 'application/json', ...init.headers },
  });
  const body = await response.json();
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((e: { msg: string }) => e.msg).join('；')
          : detail?.message || '请求未完成，请稍后重试',
    );
  }
  return body;
}
export const post = <T>(path: string, value: unknown = {}) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(value) });
export const put = <T>(path: string, value: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(value) });
