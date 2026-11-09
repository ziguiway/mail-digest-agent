import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, post, type Task } from '@/api/client';
import type { components } from '@/api/schema';
import { Button } from '@/components/ui/button';
import { ErrorNotice, Status } from '@/components/shared';
import { useJob } from '@/hooks/use-job';
import { time } from '@/lib/utils';
type Delivery = components['schemas']['DeliveryView'];
export function DeliveryHistory() {
  const cache = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['deliveries'],
    queryFn: () => api<Delivery[]>('/deliveries'),
    refetchInterval: 2500,
  });
  const job = useJob('delivery-retry');
  const [confirmed, setConfirmed] = useState<string[]>([]);
  if (!data?.length) return error ? <ErrorNotice>{error.message}</ErrorNotice> : null;
  return (
    <section className="settings-card delivery-history">
      <div className="section-heading">
        <div>
          <h2>飞书投递记录</h2>
          <p className="muted">生成与发送分别记录，重试继续使用已保存的正文与分段回执。</p>
        </div>
      </div>
      <ErrorNotice>{job.error}</ErrorNotice>
      {data.slice(0, 20).map((row) => (
        <article className="delivery-row" key={row.id}>
          <div className="section-heading">
            <strong>
              {{
                digest: '邮件摘要',
                query: '查询回复',
                test: '连接测试',
                request_ack: '接收提示',
                request_error: '执行失败提示',
              }[row.kind] || '飞书消息'}
            </strong>
            <Status value={row.status} />
          </div>
          <p className="muted">
            {time(row.created_at)} · 接收单聊 <code>{row.recipient_chat_id}</code>
          </p>
          <p className="field-hint">
            已确认成功的分段：{row.chunks.filter((chunk) => chunk.status === 'sent').length}
          </p>
          {row.error && <p className="inline-error">{row.error}</p>}
          {row.status === 'uncertain' && (
            <label className="uncertain-choice">
              <input
                type="checkbox"
                checked={confirmed.includes(row.id)}
                onChange={(e) =>
                  setConfirmed(
                    e.target.checked
                      ? [...confirmed, row.id]
                      : confirmed.filter((id) => id !== row.id),
                  )
                }
              />
              我已核对飞书，仍要重试；可能产生重复消息。
            </label>
          )}
          {['failed', 'uncertain', 'paused'].includes(row.status) &&
            row.task_status === 'failed' && (
              <Button
                variant="outline"
                size="sm"
                disabled={job.busy || (row.status === 'uncertain' && !confirmed.includes(row.id))}
                onClick={async () => {
                  await job.run(() =>
                    post<Task>(`/deliveries/${row.id}/retry`, {
                      allow_uncertain: confirmed.includes(row.id),
                    }),
                  );
                  cache.invalidateQueries({ queryKey: ['deliveries'] });
                }}
              >
                重试未确认成功的分段
              </Button>
            )}
        </article>
      ))}
    </section>
  );
}
