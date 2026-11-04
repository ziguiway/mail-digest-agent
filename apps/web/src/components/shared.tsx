import { useState, type ReactNode } from 'react';
import { ArrowRight, CheckCircle, CircleNotch, WarningCircle } from '@phosphor-icons/react';
import { useQuery } from '@tanstack/react-query';
import { api, type Mail } from '@/api/client';
import { cn, priorityText, statusText, time } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
export function Status({ value }: { value: string }) {
  return (
    <span
      className={cn('status', {
        success: ['verified', 'succeeded', 'generated', 'sent', 'ready', 'paired'].includes(value),
        warning: [
          'unverified',
          'queued',
          'partial',
          'pending',
          'uncertain',
          'paused',
          'pairing',
          'authorizing',
          'connecting',
        ].includes(value),
        danger: ['failed', 'error'].includes(value),
      })}
    >
      <span className="status-dot" />
      {statusText[value] || value}
    </span>
  );
}
export function Priority({ value }: { value: string }) {
  return <span className={`priority ${value}`}>{priorityText[value] || value}</span>;
}
export function ErrorNotice({ children }: { children?: ReactNode }) {
  return children ? (
    <div className="notice error" role="alert">
      <WarningCircle size={18} />
      <span>{children}</span>
    </div>
  ) : null;
}
export function CoverageNotice({ warnings }: { warnings: string[] }) {
  return warnings.length ? (
    <div className="notice warning" role="status">
      <WarningCircle size={18} />
      <div>
        <strong>检索覆盖提示</strong>
        {[...new Set(warnings)].map((warning) => (
          <p key={warning}>{warning}</p>
        ))}
      </div>
    </div>
  ) : null;
}
export function Empty({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <CheckCircle size={28} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading">
      <CircleNotch className="spin" size={22} />
      <span>正在读取工作台</span>
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
export function JobNotice({ phase, busy }: { phase?: string; busy: boolean }) {
  return busy ? (
    <div className="notice">
      <CircleNotch className="spin" size={18} />
      <span>{phase || '等待执行'}，你可以继续浏览。</span>
    </div>
  ) : null;
}
export function CacheStatus({ state }: { state: Mail['observed_state'] }) {
  return (
    <span className="cache-state" data-state={state}>
      {state === 'current' ? '最近观察到' : state === 'historical' ? '历史缓存' : '尚未确认'}
    </span>
  );
}

export function MailDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [navigation, setNavigation] = useState<{ root: string | null; shown: string | null }>({
    root: null,
    shown: null,
  });
  const shown = navigation.root === id ? navigation.shown : id;
  const { data, error, isLoading } = useQuery({
    queryKey: ['mail', shown],
    queryFn: () => api<Mail>(`/mails/${shown}`),
    enabled: !!shown,
  });
  const thread = useQuery({
    queryKey: ['mail-thread', shown],
    queryFn: () =>
      api<{ linked: Mail[]; subject_candidates: Mail[]; truncated: boolean }>(
        `/mails/${shown}/thread`,
      ),
    enabled: !!shown,
  });
  function openMail(mailId: string) {
    setNavigation({ root: id, shown: mailId });
  }
  return (
    <Dialog
      open={!!id}
      onOpenChange={(open) => {
        if (!open) {
          setNavigation({ root: null, shown: null });
          onClose();
        }
      }}
    >
      <DialogContent className="mail-dialog">
        <DialogHeader>
          <DialogTitle>{data?.subject || '邮件详情'}</DialogTitle>
          <DialogDescription>只读查看邮件正文与来源。</DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <Loading />
        ) : error ? (
          <ErrorNotice>{error.message}</ErrorNotice>
        ) : (
          data && (
            <>
              {shown !== id && (
                <Button variant="ghost" size="sm" onClick={() => openMail(id!)}>
                  返回最初邮件
                </Button>
              )}
              <div className="mail-metadata">
                <span>{data.account_label}</span>
                <span>文件夹：{data.folder}</span>
                <CacheStatus state={data.observed_state} />
                <span>{data.sender}</span>
                <span>
                  {data.received_at ? '收到' : '发送'}于 {time(data.received_at || data.sent_at)}
                </span>
                {data.last_observed_at && <span>最近观察：{time(data.last_observed_at)}</span>}
              </div>
              {data.observed_state !== 'current' && (
                <div className="notice warning">
                  {data.observed_state === 'historical'
                    ? '这是历史缓存，不表示邮件仍在当前同步文件夹中，也不等同于已从邮箱删除。'
                    : '邮件正文已缓存，当前邮箱中的观察状态尚未确认。'}
                </div>
              )}
              <div className="mail-body">{data.body || '这封邮件没有可提取的正文。'}</div>
              {data.body_truncated && (
                <div className="notice warning">正文较长，当前展示提取的片段。</div>
              )}
              <section className="mail-thread" aria-label="相关往来">
                <h3>相关往来</h3>
                <p className="muted">按邮件引用头关联，点击可核对正文。</p>
                {thread.isLoading ? (
                  <Loading />
                ) : thread.error ? (
                  <>
                    <ErrorNotice>{thread.error.message}</ErrorNotice>
                    <Button size="sm" variant="outline" onClick={() => thread.refetch()}>
                      重试读取往来
                    </Button>
                  </>
                ) : (
                  <>
                    {thread.data?.linked
                      .filter((mail) => mail.id !== shown)
                      .map((mail) => (
                        <Button
                          variant="ghost"
                          className="thread-link"
                          key={mail.id}
                          onClick={() => openMail(mail.id)}
                        >
                          <span>
                            <strong>{mail.subject}</strong>
                            <small>
                              {mail.account_label} · {time(mail.received_at || mail.sent_at)}
                            </small>
                          </span>
                          <CacheStatus state={mail.observed_state} />
                          <ArrowRight size={16} />
                        </Button>
                      ))}
                    {thread.data?.linked.length === 1 && (
                      <p className="muted">缓存中尚无其他引用关联的往来。</p>
                    )}
                    {!!thread.data?.subject_candidates.length && (
                      <details className="thread-candidates">
                        <summary>同标题候选（尚未确认关联）</summary>
                        {thread.data.subject_candidates.map((mail) => (
                          <Button
                            variant="ghost"
                            className="thread-link"
                            key={mail.id}
                            onClick={() => openMail(mail.id)}
                          >
                            {mail.subject}
                            <ArrowRight size={16} />
                          </Button>
                        ))}
                      </details>
                    )}
                    {thread.data?.truncated && (
                      <p className="muted">往来较多，当前只显示部分缓存结果。</p>
                    )}
                  </>
                )}
              </section>
            </>
          )
        )}
      </DialogContent>
    </Dialog>
  );
}
export function TextLink({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <Button variant="ghost" className="text-link" onClick={onClick}>
      {children}
      <ArrowRight size={15} />
    </Button>
  );
}
