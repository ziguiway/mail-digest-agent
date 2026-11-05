import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, Clock, ArrowClockwise } from '@phosphor-icons/react';
import { api, post, type Account, type Digest, type DigestItem, type Task } from '@/api/client';
import { Button } from '@/components/ui/button';
import {
  CoverageNotice,
  Empty,
  ErrorNotice,
  JobNotice,
  Loading,
  MailDialog,
  PageHeading,
  Priority,
  Status,
} from '@/components/shared';
import { useJob } from '@/hooks/use-job';
import { time, statusText } from '@/lib/utils';
import type { FeishuState } from './feishu';

type RefreshResult = {
  digest_id?: string | null;
  partial_digest_id?: string | null;
  message?: string;
  empty?: boolean;
  reused?: boolean;
  added?: number;
  removed?: number;
  account_ids?: string[];
  group_id?: string;
  range_end?: string;
};
function sameScope(a: string[], b: string[]) {
  return a.slice().sort().join('|') === b.slice().sort().join('|');
}
function rangeTime(value: string, zone?: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: zone || 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}
function warnings(digest: Digest) {
  return digest.coverage.flatMap((raw) => {
    const row = raw as { label?: string; warnings?: string[]; error?: string };
    return [...(row.warnings || []), ...(row.error ? [row.error] : [])].map(
      (note) => `${row.label || '邮箱'}：${note}`,
    );
  });
}

export function DigestsPage() {
  const { data, error } = useQuery({
    queryKey: ['digests'],
    queryFn: () => api<Digest[]>('/digests'),
    // Scheduled, Feishu and first-run tasks can finish outside this page.
    refetchInterval: 5000,
  });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/accounts') });
  const feishu = useQuery({
    queryKey: ['feishu'],
    queryFn: () => api<FeishuState>('/settings/feishu'),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [accountIds, setAccountIds] = useState<string[] | null>(null);
  const [filter, setFilter] = useState('all');
  const [mailId, setMailId] = useState<string | null>(null);
  const job = useJob();
  const sending = useJob('digest-send');
  const pendingSelection = useRef<string | null>(null);
  const enabled = (accounts.data || []).filter((a) => a.enabled);
  const chosen = accountIds ?? enabled.map((a) => a.id);
  const result = (job.job?.status === 'succeeded' ? job.job.result || {} : {}) as RefreshResult;
  const matches = !!result.account_ids && sameScope(result.account_ids, chosen);
  useEffect(() => {
    if (job.job && ['queued', 'running'].includes(job.job.status))
      pendingSelection.current = job.job.id;
    if (job.job?.status === 'succeeded' && pendingSelection.current === job.job.id) {
      const value = (job.job.result || {}) as RefreshResult;
      if (value.group_id) setSelected(value.digest_id || null);
      setFilter('all');
      pendingSelection.current = null;
    }
  }, [job.job?.id, job.job?.status]);
  const groups = new Map<string, Digest[]>();
  for (const d of data || []) {
    if (!d.legacy && d.group_id && sameScope(d.account_ids || [], chosen)) {
      groups.set(d.group_id, [...(groups.get(d.group_id) || []), d]);
    }
  }
  const legacy = (data || []).filter((d) => d.legacy);
  const latest = [...groups.values()][0];
  const first =
    latest?.find((d) => d.status === 'generated') ||
    latest?.[0] ||
    (accountIds === null ? legacy.find((d) => d.items.length > 0) : undefined);
  const feedbackFresh =
    matches &&
    !!result.range_end &&
    (!first || result.range_end >= (first.checked_end || first.range_end || first.created_at));
  const checkedEmpty = feedbackFresh && result.empty && !result.digest_id;
  const digest =
    data?.find((d) => d.id === selected) ||
    (checkedEmpty ? undefined : chosen.length ? first : undefined);
  const items = (digest?.items || []) as unknown as DigestItem[];
  const filtered = items.filter((i) => filter === 'all' || i.priority === filter);
  function selectDigest(id: string) {
    setSelected(id);
    setFilter('all');
  }
  function toggleAccount(id: string) {
    setAccountIds(chosen.includes(id) ? chosen.filter((value) => value !== id) : [...chosen, id]);
    setSelected(null);
  }
  function refresh(force = false) {
    setSelected(null);
    setFilter('all');
    void job.run(() =>
      post<Task>('/digests', {
        account_ids: chosen,
        force_reanalyze: force,
        request_id: crypto.randomUUID(),
      }),
    );
  }
  return (
    <>
      <div className="digest-page-heading">
        <PageHeading
          title="来信摘要"
          description="汇总最近 24 小时的完整来信，刷新后继续保留已分析的邮件。"
          actions={
            <>
              <Button
                variant="outline"
                className="transition-colors"
                disabled={job.busy || !chosen.length}
                onClick={() => refresh(true)}
              >
                重新分析
              </Button>
              <Button
                className="transition-colors"
                disabled={job.busy || !chosen.length}
                onClick={() => refresh()}
              >
                <ArrowClockwise />
                刷新摘要
              </Button>
            </>
          }
        />
      </div>
      <div className="digest-range-toolbar">
        <span className="digest-window-label">
          <Clock size={17} />
          最近 24 小时
        </span>
        <details className="digest-account-picker">
          <summary>{accountIds === null ? '全部启用邮箱' : `已选 ${chosen.length} 个邮箱`}</summary>
          <fieldset disabled={job.busy}>
            <legend className="sr-only">摘要来源邮箱</legend>
            {enabled.map((a) => (
              <label key={a.id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(a.id)}
                  onChange={() => toggleAccount(a.id)}
                />
                {a.label}
              </label>
            ))}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setAccountIds(null);
                setSelected(null);
              }}
            >
              选择全部
            </Button>
          </fieldset>
        </details>
        <span className="muted">刷新仅更新网页，推送需单独操作。</span>
      </div>
      <ErrorNotice>{error?.message || job.error || digest?.refresh_error}</ErrorNotice>
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      {feedbackFresh &&
        (!selected || selected === result.digest_id) &&
        result.message &&
        !job.busy && (
          <div className="notice" role="status">
            {result.message}
            {!result.reused &&
              !result.empty &&
              (!!result.added || !!result.removed) &&
              ` · 新增 ${result.added || 0} 封，移出范围 ${result.removed || 0} 封`}
            {result.partial_digest_id && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => selectDigest(result.partial_digest_id!)}
              >
                查看本次部分结果
              </Button>
            )}
          </div>
        )}
      {!data || !accounts.data ? (
        <Loading />
      ) : (
        <div className="digest-layout">
          <aside className="digest-history" aria-label="摘要历史">
            <p className="eyebrow">HISTORY</p>
            {[...groups.entries()].map(([id, versions]) => {
              const current = versions.find((d) => d.status === 'generated') || versions[0];
              return (
                <div className="digest-history-group" key={id}>
                  <button
                    className={current.id === digest?.id ? 'active' : ''}
                    onClick={() => selectDigest(current.id)}
                  >
                    <strong>{current.title}</strong>
                    <span>
                      {current.source_count} 封邮件 · 版本 {current.revision}
                    </span>
                  </button>
                  {versions.length > 1 && (
                    <details>
                      <summary>历史版本（{versions.length}）</summary>
                      {versions.map((d) => (
                        <button
                          className={d.id === digest?.id ? 'active' : ''}
                          key={d.id}
                          onClick={() => selectDigest(d.id)}
                        >
                          <strong>
                            版本 {d.revision} · {d.items.length} 件事项
                          </strong>
                          <span>
                            {time(d.created_at)}
                            {d.status === 'partial' ? ' · 部分结果' : ''}
                          </span>
                        </button>
                      ))}
                    </details>
                  )}
                </div>
              );
            })}
            {!!legacy.length && (
              <details className="legacy-digests">
                <summary>旧版摘要（{legacy.length}）</summary>
                <p className="field-hint">按旧版增量规则生成，保留原内容与发送记录。</p>
                {legacy.map((d) => (
                  <button
                    key={d.id}
                    className={d.id === digest?.id ? 'active' : ''}
                    onClick={() => selectDigest(d.id)}
                  >
                    <strong>{d.title}</strong>
                    <span>
                      {time(d.created_at)} · {d.items.length} 件事项
                    </span>
                  </button>
                ))}
              </details>
            )}
            {!groups.size && !legacy.length && (
              <p className="muted">有内容变化时，会在这里保存版本。</p>
            )}
          </aside>
          <section className="panel digest-detail">
            {!digest ? (
              <Empty
                title={
                  !chosen.length
                    ? '请选择至少一个邮箱'
                    : checkedEmpty
                      ? '最近 24 小时没有邮件'
                      : '生成第一份最近 24 小时摘要'
                }
                description={
                  checkedEmpty
                    ? '所选邮箱的时间范围已检查，之后可以随时刷新。'
                    : '刷新会汇总所选邮箱在这个时间范围内的完整来信。'
                }
                action={
                  !enabled.length ? (
                    <Button variant="outline" asChild>
                      <Link to="/setup">连接邮箱</Link>
                    </Button>
                  ) : undefined
                }
              />
            ) : (
              <>
                <div className="section-heading">
                  <div>
                    <p className="eyebrow">MAIL BRIEFING</p>
                    <h2>{digest.title}</h2>
                  </div>
                  <Status value={digest.status} />
                </div>
                {digest.range_start && digest.range_end ? (
                  <>
                    <p className="digest-range">
                      范围：{rangeTime(digest.checked_start || digest.range_start, digest.timezone)}
                      —{rangeTime(digest.checked_end || digest.range_end, digest.timezone)}（
                      {digest.timezone}）
                    </p>
                    <p className="muted">
                      最后检查 {time(digest.checked_at || digest.created_at)} · 内容更新{' '}
                      {time(digest.created_at)} · 版本 {digest.revision} · {digest.source_count}{' '}
                      封邮件
                    </p>
                    <details className="digest-version-details">
                      <summary>版本信息</summary>
                      <p>
                        本版本生成范围：{rangeTime(digest.range_start, digest.timezone)}—
                        {rangeTime(digest.range_end, digest.timezone)}。已保存的内容与来源保持不变。
                      </p>
                    </details>
                  </>
                ) : (
                  <p className="muted">
                    {time(digest.created_at)} 生成 · 旧版增量摘要，未记录确切时间范围。
                  </p>
                )}
                <p className="field-hint">
                  {digest.delivery_status === 'not_configured'
                    ? '尚未推送至飞书'
                    : `推送状态：${statusText[digest.delivery_status] || digest.delivery_status}`}
                </p>
                {feishu.data?.configured && (
                  <div className="digest-send-action">
                    <p className="field-hint">通过已连接的飞书机器人发送这份摘要。</p>
                    <Button
                      variant="outline"
                      disabled={
                        !feishu.data.enabled ||
                        sending.busy ||
                        !items.length ||
                        digest.delivery_status === 'sent'
                      }
                      onClick={() =>
                        sending.run(() =>
                          post<Task>(`/digests/${digest.id}/send`, {
                            binding_id: feishu.data!.binding_id,
                          }),
                        )
                      }
                    >
                      {digest.delivery_status === 'sent' ? '已推送' : '推送这份摘要到飞书'}
                    </Button>
                    <ErrorNotice>{sending.error}</ErrorNotice>
                    <JobNotice busy={sending.busy} phase={sending.job?.phase} />
                  </div>
                )}
                <CoverageNotice warnings={warnings(digest)} />
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
                        {key === 'all'
                          ? items.length
                          : items.filter((i) => i.priority === key).length}
                      </span>
                    </button>
                  ))}
                </div>
                {filtered.map((item) => (
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
                {!filtered.length && (
                  <Empty
                    title={
                      items.length
                        ? '该分类暂无事项'
                        : digest.legacy
                          ? '这份旧版摘要没有待汇总内容'
                          : '最近 24 小时没有邮件'
                    }
                    description={
                      items.length
                        ? `全部摘要仍有 ${items.length} 件事项。`
                        : '可以刷新时间范围，历史版本仍然保留。'
                    }
                    action={
                      items.length ? (
                        <Button variant="outline" onClick={() => setFilter('all')}>
                          查看全部 {items.length} 条
                        </Button>
                      ) : undefined
                    }
                  />
                )}
              </>
            )}
          </section>
        </div>
      )}
      <MailDialog
        id={mailId}
        onClose={() => setMailId(null)}
        digestId={digest?.legacy ? undefined : digest?.id}
      />
    </>
  );
}
