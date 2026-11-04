import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  ArrowUp,
  ArrowClockwise,
  ChatsCircle,
  CheckCircle,
  Clock,
  EnvelopeSimple,
  MagnifyingGlass,
  Plus,
  Sparkle,
} from '@phosphor-icons/react';
import {
  api,
  post,
  type Account,
  type Conversation,
  type Digest,
  type DigestItem,
  type Mail,
  type Message,
  type Overview,
  type Source,
  type Task,
} from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  CoverageNotice,
  Empty,
  ErrorNotice,
  JobNotice,
  Loading,
  MailDialog,
  CacheStatus,
  PageHeading,
  Priority,
  Status,
} from '@/components/shared';
import { useJob } from '@/hooks/use-job';
import { DeliveryHistory } from '@/components/delivery-history';
import type { FeishuState } from './feishu';
import { time, statusText } from '@/lib/utils';

const digestItems = (digest?: Digest | null) => (digest?.items || []) as unknown as DigestItem[];
function digestWarnings(digest?: Digest | null): string[] {
  return (digest?.coverage || []).flatMap((value) => {
    const row = value as { label?: string; error?: string; warnings?: string[] };
    return row.error
      ? [`${row.label || '邮箱'}：${row.error}`]
      : (row.warnings || []).map((text) => `${row.label || '邮箱'}：${text}`);
  });
}
function useDigests() {
  return useQuery({
    queryKey: ['digests'],
    queryFn: () => api<Digest[]>('/digests'),
    refetchInterval: 2500,
  });
}

export function OverviewPage() {
  const { data, error } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api<Overview>('/overview'),
    refetchInterval: 5000,
  });
  const job = useJob();
  if (error) return <ErrorNotice>{error.message}</ErrorNotice>;
  if (!data) return <Loading />;
  const latest = data.latest_digest;
  const items = digestItems(latest);
  const high = items.filter((i) => i.priority === 'high');
  return (
    <>
      <PageHeading
        eyebrow="YOUR MAIL, IN ORDER"
        title="让重要的来信，先被看见。"
        description="把分散的邮箱放在一起，读懂事项，再决定从哪里开始。"
        actions={
          <Button onClick={() => job.run(() => post<Task>('/digests'))} disabled={job.busy}>
            <Sparkle />
            生成摘要
          </Button>
        }
      />
      {!data.onboarding.complete && (
        <div className="setup-prompt">
          <div>
            <strong>把工作台设置好</strong>
            <p>连接模型和邮箱，试运行一份摘要，之后每天按时送达。</p>
          </div>
          <Button asChild variant="outline">
            <Link to="/setup">
              继续配置 <ArrowRight />
            </Link>
          </Button>
        </div>
      )}
      <ErrorNotice>{job.error}</ErrorNotice>
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      <div className="metrics">
        <div>
          <span>已连接邮箱</span>
          <strong>
            {data.accounts.filter((a) => a.enabled).length}
            <small>个来源</small>
          </strong>
          <p>
            {data.accounts
              .filter((a) => a.enabled)
              .map((a) =>
                a.provider === 'qq' ? 'QQ 邮箱' : a.provider === '163' ? '网易邮箱' : a.label,
              )
              .join(' · ') || '等待添加邮箱'}
          </p>
        </div>
        <div>
          <span>已归集邮件</span>
          <strong>
            {data.mail_count}
            <small>封</small>
          </strong>
          <p>保留来源，随时查找与追问</p>
        </div>
        <div>
          <span>最近摘要中优先处理</span>
          <strong>
            {high.length}
            <small>件事项</small>
          </strong>
          <p>{latest ? `${time(latest.created_at)} 更新` : '生成首份摘要后显示'}</p>
        </div>
        <div>
          <span>每日汇总</span>
          <strong>{data.schedule.enabled ? data.schedule.digest_time : '未启用'}</strong>
          <p>
            {data.schedule.timezone} · {data.worker.alive ? '后台运行中' : '后台尚未就绪'}
          </p>
        </div>
      </div>
      <div className="overview-grid">
        <section className="panel focus-panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">FOCUS FIRST</p>
              <h2>最近一份来信摘要</h2>
            </div>
            <Link className="quiet-link" to="/digests">
              查看全部 <ArrowRight />
            </Link>
          </div>
          {latest ? (
            <>
              <div className="digest-meta">
                <span>{latest.title}</span>
                <Status value={latest.status} />
              </div>
              {items.length ? (
                items
                  .slice()
                  .sort(
                    (a, b) =>
                      ({ high: 0, medium: 1, low: 2 })[a.priority] -
                      { high: 0, medium: 1, low: 2 }[b.priority],
                  )
                  .slice(0, 4)
                  .map((item) => (
                    <div className="focus-row" key={item.mail_id}>
                      <Priority value={item.priority} />
                      <div>
                        <h3>{item.summary}</h3>
                        <p>{item.action}</p>
                      </div>
                      <span className="deadline">{item.deadline || '未明确截止'}</span>
                    </div>
                  ))
              ) : (
                <Empty
                  title="这次没有新增待汇总邮件"
                  description="新的来信会在下次同步后出现在这里。"
                />
              )}
            </>
          ) : (
            <Empty
              title="还没有摘要"
              description="连接邮箱后，生成第一份带事项与优先级的摘要。"
              action={
                <Button variant="outline" asChild>
                  <Link to="/setup">开始配置</Link>
                </Button>
              }
            />
          )}
        </section>
        <aside className="stack">
          <section className="panel">
            <div className="section-heading">
              <h2>邮箱连接</h2>
              <Link className="quiet-link" to="/connections">
                管理
              </Link>
            </div>
            {data.accounts.length ? (
              data.accounts.map((a) => (
                <div className="connection-row" key={a.id}>
                  <div className={`provider-mark ${a.provider}`}>
                    {a.provider === 'qq' ? 'Q' : a.provider === '163' ? '163' : 'M'}
                  </div>
                  <div>
                    <strong>{a.label}</strong>
                    <p>{a.email}</p>
                  </div>
                  <span
                    className={`connection-dot ${a.error ? 'bad' : a.status === 'verified' ? 'good' : ''}`}
                    title={a.error || a.status}
                  />
                </div>
              ))
            ) : (
              <p className="muted">添加 QQ、网易或其他 IMAP 邮箱。</p>
            )}
          </section>
          <section className="query-invitation">
            <ChatsCircle size={28} />
            <h2>有个问题，直接问。</h2>
            <p>
              “导师的会议通知后来有更新吗？”
              <br />
              从多封往来中找答案，并保留依据。
            </p>
            <Button variant="outline" asChild>
              <Link to="/chat">
                打开智能查询 <ArrowRight />
              </Link>
            </Button>
          </section>
        </aside>
      </div>
      <section className="panel">
        <div className="section-heading">
          <h2>最近执行</h2>
          <Link className="quiet-link" to="/tasks">
            任务记录 <ArrowRight />
          </Link>
        </div>
        <TaskRows tasks={data.tasks} />
      </section>
    </>
  );
}

export function MailsPage() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [keyword, setKeyword] = useState('');
  const [account, setAccount] = useState('');
  const [cacheState, setCacheState] = useState('all');
  const [offset, setOffset] = useState(0);
  const job = useJob();
  useEffect(() => {
    const id = window.setTimeout(() => {
      setKeyword(query);
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(id);
  }, [query]);
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/accounts') });
  const { data, isLoading, error } = useQuery({
    queryKey: ['mails', keyword, account, cacheState, offset],
    queryFn: () =>
      api<{ mails: Mail[]; total: number; has_more: boolean; warnings: string[] }>(
        `/mails?q=${encodeURIComponent(keyword)}&account_id=${account}&cache_state=${cacheState}&offset=${offset}&limit=30`,
      ),
  });
  return (
    <>
      <PageHeading
        title="邮件"
        description="来自多个邮箱的来信，保留各自的来源与上下文。"
        actions={
          <Button
            variant="outline"
            disabled={job.busy}
            onClick={() =>
              job.run(() => post<Task>('/sync', { account_ids: account ? [account] : [] }))
            }
          >
            <ArrowClockwise />
            立即同步
          </Button>
        }
      />
      <ErrorNotice>{error?.message || job.error}</ErrorNotice>
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      <div className="toolbar">
        <div className="search-field">
          <MagnifyingGlass />
          <Input
            aria-label="搜索邮件"
            placeholder="搜索主题、发件人或正文关键词"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <select
          className="native-select"
          aria-label="来源邮箱"
          value={account}
          onChange={(e) => {
            setAccount(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">全部启用邮箱</option>
          {accounts.data
            ?.filter((a) => a.enabled)
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
        </select>
        <select
          className="native-select"
          aria-label="缓存状态"
          value={cacheState}
          onChange={(e) => {
            setCacheState(e.target.value);
            setOffset(0);
          }}
        >
          <option value="all">全部缓存状态</option>
          <option value="current">最近观察到</option>
          <option value="historical">历史缓存</option>
          <option value="unknown">尚未确认</option>
        </select>
        <span className="muted">{data?.total || 0} 封</span>
      </div>
      <CoverageNotice warnings={data?.warnings || []} />
      <section className="panel mail-list">
        {isLoading ? (
          <Loading />
        ) : !data?.mails.length ? (
          <Empty
            title="当前没有匹配邮件"
            description={
              cacheState !== 'all'
                ? '当前状态没有匹配邮件，可切换到全部缓存状态查看。'
                : keyword
                  ? '试试更短的关键词，或选择其他邮箱。'
                  : '完成邮箱配置并同步后，来信会出现在这里。'
            }
          />
        ) : (
          data.mails.map((mail) => (
            <button className="mail-row" key={mail.id} onClick={() => setParams({ mail: mail.id })}>
              <div className="mail-avatar">{mail.sender.slice(0, 1)}</div>
              <div className="mail-row-content">
                <div>
                  <strong>{mail.sender.split('<')[0].trim()}</strong>
                  <span>{time(mail.received_at || mail.sent_at)}</span>
                </div>
                <h3>{mail.subject}</h3>
                <p>{mail.body.slice(0, 100)}</p>
                <small>
                  {mail.account_label} · {mail.folder} <CacheStatus state={mail.observed_state} />
                </small>
              </div>
              {mail.priority && <Priority value={mail.priority} />}
            </button>
          ))
        )}
      </section>
      {data && data.total > 30 && (
        <div className="pagination">
          <Button
            variant="outline"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            上一页
          </Button>
          <span>
            {offset + 1}—{Math.min(offset + 30, data.total)} / {data.total}
          </span>
          <Button
            variant="outline"
            disabled={!data.has_more}
            onClick={() => setOffset(offset + 30)}
          >
            下一页
          </Button>
        </div>
      )}
      <MailDialog id={params.get('mail')} onClose={() => setParams({})} />
    </>
  );
}

export function DigestsPage() {
  const { data, error } = useDigests();
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState('all');
  const [mailId, setMailId] = useState<string | null>(null);
  const job = useJob();
  const sending = useJob('digest-send');
  const feishu = useQuery({
    queryKey: ['feishu'],
    queryFn: () => api<FeishuState>('/settings/feishu'),
  });
  const digest = data?.find((d) => d.id === selected) || data?.[0];
  const items = digestItems(digest);
  return (
    <>
      <PageHeading
        title="来信摘要"
        description="把邮件变成清楚的事项，按优先级安排下一步。"
        actions={
          <Button disabled={job.busy} onClick={() => job.run(() => post<Task>('/digests'))}>
            <Sparkle />
            生成新摘要
          </Button>
        }
      />
      <ErrorNotice>{error?.message || job.error}</ErrorNotice>
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      {!data ? (
        <Loading />
      ) : !data.length ? (
        <section className="panel">
          <Empty
            title="第一份摘要，还在等你"
            description="完成模型与邮箱配置后，试着生成一份摘要。"
            action={
              <Button variant="outline" asChild>
                <Link to="/setup">打开配置向导</Link>
              </Button>
            }
          />
        </section>
      ) : (
        <div className="digest-layout">
          <aside className="digest-history">
            <p className="eyebrow">HISTORY</p>
            {data.map((d) => (
              <button
                key={d.id}
                className={d.id === digest?.id ? 'active' : ''}
                onClick={() => setSelected(d.id)}
              >
                <strong>{d.title}</strong>
                <span>
                  {time(d.created_at)} · {d.items.length} 件事项
                </span>
              </button>
            ))}
          </aside>
          <section className="panel digest-detail">
            <div className="section-heading">
              <div>
                <p className="eyebrow">MAIL BRIEFING</p>
                <h2>{digest?.title}</h2>
              </div>
              <Status value={digest?.status || 'generated'} />
            </div>
            <p className="muted">
              {time(digest?.created_at)} 生成 ·{' '}
              {digest?.delivery_status === 'not_configured'
                ? '保存在工作台，飞书尚未启用'
                : '推送状态：' +
                  (statusText[digest?.delivery_status || ''] || digest?.delivery_status)}
            </p>
            {feishu.data?.configured && (
              <div className="digest-send-action">
                <p className="field-hint">
                  以应用机器人身份推送到配对单聊：<code>{feishu.data.chat_id || '尚未配对'}</code>
                </p>
                <Button
                  variant="outline"
                  disabled={!feishu.data.enabled || sending.busy || !digest}
                  onClick={() =>
                    sending.run(() =>
                      post<Task>(`/digests/${digest!.id}/send`, {
                        binding_id: feishu.data!.binding_id,
                      }),
                    )
                  }
                >
                  推送这份摘要到飞书
                </Button>
                <ErrorNotice>{sending.error}</ErrorNotice>
                <JobNotice busy={sending.busy} phase={sending.job?.phase} />
              </div>
            )}
            <CoverageNotice warnings={digestWarnings(digest)} />
            <div className="filter-tabs" role="tablist" aria-label="摘要优先级">
              {[
                ['all', '全部'],
                ['high', '优先处理'],
                ['medium', '需要关注'],
                ['low', '信息通知'],
              ].map(([key, label]) => (
                <button
                  role="tab"
                  aria-selected={filter === key}
                  className={filter === key ? 'active' : ''}
                  key={key}
                  onClick={() => setFilter(key)}
                >
                  {label}
                  <span>
                    {key === 'all' ? items.length : items.filter((i) => i.priority === key).length}
                  </span>
                </button>
              ))}
            </div>
            {items
              .filter((i) => filter === 'all' || i.priority === filter)
              .map((item) => (
                <article className="digest-item" key={item.mail_id}>
                  <Priority value={item.priority} />
                  <h3>{item.summary}</h3>
                  <p>{item.action}</p>
                  <div className="item-facts">
                    <span>
                      <Clock size={15} />
                      {item.deadline || '截止时间未明确'}
                    </span>
                    <Button variant="ghost" size="sm" onClick={() => setMailId(item.mail_id)}>
                      查看邮件依据 <ArrowRight size={14} />
                    </Button>
                  </div>
                  <div className="reason">判断依据：{item.reason}</div>
                </article>
              ))}
            {!items.filter((i) => filter === 'all' || i.priority === filter).length && (
              <Empty title="这一类暂无事项" description="切换分类查看其他邮件，或等待新的来信。" />
            )}
          </section>
        </div>
      )}
      <MailDialog id={mailId} onClose={() => setMailId(null)} />
    </>
  );
}

export function ChatPage() {
  const cache = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const pendingRequest = useRef<{ signature: string; id: string } | null>(null);
  const [mailId, setMailId] = useState<string | null>(null);
  const job = useJob('query');
  useEffect(() => {
    if (job.job?.payload.conversation_id)
      setConversationId(String(job.job.payload.conversation_id));
  }, [job.job?.id]);
  const conversations = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api<Conversation[]>('/conversations'),
  });
  const messages = useQuery({
    queryKey: ['messages', conversationId],
    queryFn: () => api<Message[]>(`/conversations/${conversationId}/messages`),
    enabled: !!conversationId,
  });
  async function send(value = question) {
    if (!value.trim() || job.busy) return;
    const signature = JSON.stringify([conversationId, value.trim()]);
    if (pendingRequest.current?.signature !== signature)
      pendingRequest.current = { signature, id: crypto.randomUUID() };
    const task = await job.run(() =>
      post<Task>('/questions', {
        question: value.trim(),
        conversation_id: conversationId,
        request_id: pendingRequest.current!.id,
      }),
    );
    if (task) {
      pendingRequest.current = null;
      setConversationId(String(task.payload.conversation_id));
      setQuestion('');
      cache.invalidateQueries({ queryKey: ['conversations'] });
      cache.invalidateQueries({ queryKey: ['messages'] });
    }
  }
  useEffect(() => {
    if (job.job?.status === 'succeeded') cache.invalidateQueries({ queryKey: ['messages'] });
  }, [job.job?.status, cache]);
  return (
    <>
      <PageHeading title="智能查询" description="从邮件中找答案，继续追问，核对每一条依据。" />
      <div className="chat-layout">
        <aside className="conversation-list">
          <Button variant="outline" className="full-width" onClick={() => setConversationId(null)}>
            <Plus />
            新建对话
          </Button>
          {conversations.data?.map((c) => (
            <button
              className={conversationId === c.id ? 'active' : ''}
              onClick={() => setConversationId(c.id)}
              key={c.id}
            >
              <ChatsCircle size={17} />
              <span>{c.title}</span>
            </button>
          ))}
        </aside>
        <section className="panel chat-panel">
          <div className="chat-messages" aria-live="polite">
            {!messages.data?.length ? (
              <div className="chat-welcome">
                <div className="chat-symbol">
                  <ChatsCircle size={32} />
                </div>
                <h2>让问题，找到来信里的答案。</h2>
                <p>查询事项、找一封邮件，或核对前后往来的变化。</p>
                <div className="suggestions">
                  {[
                    '今天有什么需要我处理的邮件？',
                    '找一下导师的会议通知。',
                    '会议安排后来有更新吗？',
                  ].map((s) => (
                    <button onClick={() => void send(s)} key={s}>
                      {s}
                      <ArrowRight size={16} />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.data.map((message) => (
                <article className={`chat-message ${message.role}`} key={message.id}>
                  <span className="message-role">
                    {message.role === 'user' ? '你' : '来信助手'}
                  </span>
                  <div className="message-content">{message.content}</div>
                  {!!message.sources.length && (
                    <div className="source-list">
                      <p>邮件依据 · {message.sources.length}</p>
                      {(message.sources as unknown as Source[]).map((source) => (
                        <button key={source.id} onClick={() => setMailId(source.id)}>
                          <EnvelopeSimple size={18} />
                          <div>
                            <strong>{source.subject}</strong>
                            <span>
                              {source.account_label} · {time(source.sent_at)}
                            </span>
                          </div>
                          <ArrowRight size={16} />
                        </button>
                      ))}
                    </div>
                  )}
                </article>
              ))
            )}
            {job.busy && (
              <div className="thinking">
                <span className="pulse-dot" />
                {job.job?.phase || '正在查找相关邮件'}
              </div>
            )}
          </div>
          <div className="chat-compose">
            <ErrorNotice>{job.error}</ErrorNotice>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <Textarea
                aria-label="邮件问题"
                placeholder="问问你的邮件，例如：那封会议通知后来有更新吗？"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    void send();
                  }
                }}
              />
              <Button
                type="submit"
                disabled={!question.trim() || job.busy}
                size="icon"
                aria-label="发送问题"
              >
                <ArrowUp />
              </Button>
            </form>
            <p>
              回答来自当前可检索的邮件。重要事项请核对原文。
              <span>Enter 发送 · Shift + Enter 换行</span>
            </p>
          </div>
        </section>
      </div>
      <MailDialog id={mailId} onClose={() => setMailId(null)} />
    </>
  );
}

export function TaskRows({ tasks }: { tasks: Task[] }) {
  return !tasks.length ? (
    <Empty title="暂无执行记录" description="同步、分析和推送任务会记录在这里。" />
  ) : (
    <div className="task-rows">
      {tasks.map((task) => (
        <div className="task-row" key={task.id}>
          <div className="task-icon">
            {task.status === 'succeeded' ? <CheckCircle /> : <Clock />}
          </div>
          <div>
            <strong>
              {{
                sync: '同步邮箱',
                digest: '生成摘要',
                query: '邮件查询',
                model_test: '验证模型',
                account_test: '测试邮箱',
                account_folders: '读取邮箱文件夹',
                feishu_authorize: '绑定飞书',
                feishu_send: '飞书投递',
              }[task.kind] || task.kind}
            </strong>
            <p>{task.error || task.phase}</p>
          </div>
          <span className="muted task-time">{time(task.created_at)}</span>
          <Status value={task.status} />
        </div>
      ))}
    </div>
  );
}
export function TasksPage() {
  const cache = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api<Task[]>('/tasks'),
    refetchInterval: 2500,
  });
  const [retryError, setRetryError] = useState('');
  return (
    <>
      <PageHeading title="任务记录" description="看见每一次同步、分析与推送的实际进度。" />
      <ErrorNotice>{error?.message || retryError}</ErrorNotice>
      <section className="panel">
        {data ? (
          <>
            <TaskRows tasks={data} />
            {data.some((t) => t.status === 'failed') && (
              <div className="retry-list">
                {data
                  .filter((t) => t.status === 'failed' && t.kind !== 'feishu_send')
                  .map((t) => (
                    <Button
                      key={t.id}
                      variant="outline"
                      onClick={async () => {
                        try {
                          await post(`/tasks/${t.id}/retry`);
                          cache.invalidateQueries({ queryKey: ['tasks'] });
                        } catch (e) {
                          setRetryError((e as Error).message);
                        }
                      }}
                    >
                      重试 {t.kind === 'query' ? '查询' : t.kind === 'digest' ? '摘要' : '任务'} ·{' '}
                      {time(t.created_at)}
                    </Button>
                  ))}
              </div>
            )}
          </>
        ) : (
          <Loading />
        )}
      </section>
      <DeliveryHistory />
    </>
  );
}
