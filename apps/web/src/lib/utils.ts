import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
export function time(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      }).format(new Date(value))
    : '尚未同步';
}
export function relative(value?: string | null) {
  if (!value) return '尚未同步';
  const minutes = Math.floor((Date.now() - new Date(value).getTime()) / 60000);
  return minutes < 1
    ? '刚刚'
    : minutes < 60
      ? `${minutes} 分钟前`
      : minutes < 1440
        ? `${Math.floor(minutes / 60)} 小时前`
        : time(value);
}
export const statusText: Record<string, string> = {
  verified: '已验证',
  unverified: '待验证',
  unconfigured: '未配置',
  error: '连接异常',
  queued: '等待执行',
  running: '执行中',
  succeeded: '已完成',
  failed: '执行失败',
  generated: '已生成',
  partial: '部分覆盖',
  not_configured: '飞书未启用',
  pending: '等待推送',
  sent: '已推送',
  stopped: '已停止',
  removing: '移除待确认',
  removed: '连接已移除',
  authorizing: '等待授权',
  permissions_required: '待配置权限',
  ready: '消息通道就绪',
  connecting: '通道未就绪',
  pairing: '等待配对',
  paired: '已配对',
  disconnected: '已解除',
  expired: '已过期',
  cancelled: '已取消',
  sending: '发送中',
  uncertain: '发送结果不确定',
  paused: '已暂停',
  not_requested: '尚未请求推送',
};
export const priorityText: Record<string, string> = {
  high: '优先处理',
  medium: '需要关注',
  low: '信息通知',
};
