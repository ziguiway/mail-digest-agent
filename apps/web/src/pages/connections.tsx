import { lazy, Suspense, useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  Check,
  CheckCircle,
  EnvelopeSimple,
  Eye,
  EyeSlash,
  Key,
  Plus,
  Robot,
  SlidersHorizontal,
} from '@phosphor-icons/react';
import {
  api,
  post,
  put,
  type Account,
  type ModelProfile,
  type Schedule,
  type Task,
} from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Switch } from '@/components/ui/switch';
import { ErrorNotice, JobNotice, Loading, PageHeading, Status } from '@/components/shared';
import { useJob } from '@/hooks/use-job';
import { useDraft } from '@/hooks/use-draft';
import { relative } from '@/lib/utils';

const LazyFeishuPanel = lazy(() =>
  import('./feishu').then((module) => ({ default: module.FeishuPanel })),
);
export function FeishuPanel() {
  return (
    <Suspense fallback={<Loading />}>
      <LazyFeishuPanel />
    </Suspense>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="form-field">
      <Label>{label}</Label>
      {children}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}
function PasswordField({
  value,
  onChange,
  placeholder,
  label = '密钥',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  label?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-field">
      <Input
        aria-label={label}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="new-password"
      />
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        aria-label={visible ? '隐藏密钥' : '显示密钥'}
      >
        {visible ? <EyeSlash size={18} /> : <Eye size={18} />}
      </button>
    </div>
  );
}

export function ModelForm() {
  const cache = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['model'],
    queryFn: () => api<ModelProfile>('/settings/model'),
  });
  const draft = useDraft('model', { base: 'https://api.openai.com/v1', name: '' });
  const { base, name } = draft.value;
  const setBase = (base: string) => draft.update({ ...draft.value, base });
  const setName = (name: string) => draft.update({ ...draft.value, name });
  const [key, setKey] = useState('');
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState('');
  const job = useJob('model-test');
  useEffect(() => {
    if (data)
      draft.hydrate({ base: data.base_url || 'https://api.openai.com/v1', name: data.name });
  }, [data?.base_url, data?.name]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    job.setError('');
    try {
      await put('/settings/model', { base_url: base, name, api_key: key || null });
      setKey('');
      draft.clear();
      setNotice('配置已保存，请验证连接。');
      cache.invalidateQueries({ queryKey: ['model'] });
    } catch (e) {
      job.setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="settings-card">
      <div className="section-heading">
        <div>
          <h2>模型连接</h2>
          <p className="muted">使用支持结构化输出与工具调用的 OpenAI 兼容模型。</p>
        </div>
        <Status value={data?.status || 'unconfigured'} />
      </div>
      <ErrorNotice>{error?.message || job.error || data?.error}</ErrorNotice>
      <form onSubmit={save} className="form-stack">
        <Field label="服务地址" hint="填写兼容接口的基础地址，通常以 /v1 结尾。">
          <Input
            aria-label="模型服务地址"
            value={base}
            onChange={(e) => setBase(e.target.value)}
            placeholder="https://api.example.com/v1"
            required
          />
        </Field>
        <Field label="模型名称" hint="请填写服务商提供的准确模型标识。">
          <Input
            aria-label="模型名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="输入模型名称"
            required
          />
        </Field>
        <Field
          label="API Key"
          hint={
            data?.key_set
              ? '密钥已保存。留空沿用，填写新密钥会替换原值。'
              : '密钥仅用于后端连接模型服务。'
          }
        >
          <PasswordField
            value={key}
            onChange={setKey}
            placeholder={data?.key_set ? '已保存，留空沿用' : '粘贴 API Key'}
            label="模型 API Key"
          />
        </Field>
        <div className="form-actions">
          <Button type="submit" disabled={saving}>
            {saving ? '保存中' : '保存配置'}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={job.busy || !data?.key_set}
            onClick={() => job.run(() => post<Task>('/settings/model/test'))}
          >
            测试连接与能力
          </Button>
          {notice && <span className="muted">{notice}</span>}
        </div>
      </form>
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      <div className="capabilities">
        <span className={data?.capabilities?.structured_output ? 'passed' : ''}>
          <CheckCircle />
          结构化摘要 {data?.capabilities?.structured_output ? '通过' : '待验证'}
        </span>
        <span className={data?.capabilities?.tool_calling ? 'passed' : ''}>
          <CheckCircle />
          工具调用 {data?.capabilities?.tool_calling ? '通过' : '待验证'}
        </span>
      </div>
    </div>
  );
}

function AccountEditor({
  account,
  onSave,
  onClose,
}: {
  account?: Account;
  onSave: (value: Account) => void;
  onClose: () => void;
}) {
  const draft = useDraft(`account:${account?.id || 'new'}`, {
    label: account?.label || '',
    email: account?.email || '',
    provider: account?.provider || 'qq',
    host: account?.host || 'imap.qq.com',
    port: account?.port || 993,
    folder: account?.folder || 'INBOX',
  });
  const { label, email, provider, host, port, folder } = draft.value;
  const setLabel = (label: string) => draft.update({ ...draft.value, label });
  const setEmail = (email: string) => draft.update({ ...draft.value, email });
  const setHost = (host: string) => draft.update({ ...draft.value, host });
  const setPort = (port: number) => draft.update({ ...draft.value, port });
  const setFolder = (folder: string) => draft.update({ ...draft.value, folder });
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const folderJob = useJob(`folders:${account?.id || 'new'}`);
  const folderResult =
    folderJob.job?.kind === 'account_folders' && folderJob.job.status === 'succeeded'
      ? (folderJob.job.result as {
          account_id: string;
          revision: number;
          folders: { name: string; selectable: boolean }[];
        })
      : null;
  const folders =
    folderResult?.account_id === account?.id && folderResult?.revision === account?.revision
      ? folderResult?.folders
      : null;
  const connectionChanged = !!password || (!!account && port !== account.port);
  function changeProvider(provider: string) {
    draft.update({
      ...draft.value,
      provider,
      host: provider === 'qq' ? 'imap.qq.com' : provider === '163' ? 'imap.163.com' : '',
    });
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const value = {
        label,
        email,
        provider,
        host,
        port,
        folder,
        password: password || null,
        enabled: account?.enabled ?? true,
        expected_revision: account?.revision,
      };
      const saved = await (account
        ? put<Account>(`/accounts/${account.id}`, value)
        : post<Account>('/accounts', value));
      draft.clear();
      onSave(saved);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form-stack" onSubmit={save}>
      <div className="account-editor-body">
        <ErrorNotice>{error}</ErrorNotice>
        <div className="form-grid">
          <Field label="邮箱类型">
            <select
              aria-label="邮箱类型"
              className="native-select"
              value={provider}
              disabled={!!account}
              onChange={(e) => changeProvider(e.target.value)}
            >
              <option value="qq">QQ 邮箱</option>
              <option value="163">网易 163 邮箱</option>
              <option value="custom">其他 IMAP 邮箱</option>
            </select>
          </Field>
          <Field label="显示名称">
            <Input
              aria-label="邮箱显示名称"
              placeholder="例如：课程与学习"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              required
            />
          </Field>
        </div>
        <Field label="邮箱地址">
          <Input
            aria-label="邮箱地址"
            type="email"
            placeholder="你的邮箱地址"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            disabled={!!account}
          />
        </Field>
        <Field
          label="客户端授权码"
          hint={
            account
              ? '留空沿用已有授权码。更新后需要重新测试连接。'
              : '在邮箱网页中开启 IMAP 服务，并生成客户端授权码。'
          }
        >
          <PasswordField
            value={password}
            onChange={setPassword}
            placeholder={account?.secret_set ? '已保存，留空沿用' : '输入客户端授权码'}
            label="邮箱授权码"
          />
        </Field>
        <details className="advanced">
          <summary>查看服务器与文件夹设置</summary>
          <div className="form-grid">
            <Field label="IMAP 服务器">
              <Input
                aria-label="IMAP 服务器"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                disabled={!!account}
                required
              />
            </Field>
            <Field label="SSL 端口">
              <Input
                aria-label="SSL 端口"
                type="number"
                value={port}
                onChange={(e) => setPort(Number(e.target.value))}
                min={1}
                max={65535}
              />
            </Field>
          </div>
          <Field
            label="同步文件夹"
            hint="每个账号选择一个同步文件夹；切换后会重新验证与回溯，原邮件依据仍保留。"
          >
            {folders ? (
              <select
                aria-label="同步文件夹"
                className="native-select"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
              >
                {!folders.some((item) => item.name === folder && item.selectable) && (
                  <option value={folder}>{folder}（当前设置，未在可选列表确认）</option>
                )}
                {folders.map((item) => (
                  <option key={item.name} value={item.name} disabled={!item.selectable}>
                    {item.name}
                    {item.selectable ? '' : '（不可同步）'}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                aria-label="同步文件夹"
                value={folder}
                onChange={(e) => setFolder(e.target.value)}
              />
            )}
          </Field>
          <div className="form-actions">
            <Button
              type="button"
              variant="outline"
              disabled={!account || !account.enabled || connectionChanged || busy || folderJob.busy}
              onClick={() =>
                account && folderJob.run(() => post<Task>(`/accounts/${account.id}/folders`))
              }
            >
              {folderJob.busy ? '正在读取文件夹' : '读取邮箱文件夹'}
            </Button>
            <span className="muted">
              {!account
                ? '先保存邮箱，再编辑并读取文件夹。'
                : connectionChanged
                  ? '请先保存新的连接参数，再读取文件夹。'
                  : !account.enabled
                    ? '启用邮箱后可读取文件夹。'
                    : '从邮箱实际返回的列表中选择。'}
            </span>
          </div>
          <JobNotice busy={folderJob.busy} phase={folderJob.job?.phase} />
          <ErrorNotice>{folderJob.error}</ErrorNotice>
          {folders?.length === 0 && (
            <p className="muted" role="status">
              邮箱没有返回可用文件夹，请检查账号权限后重试。
            </p>
          )}
        </details>
        <div className="helper-box">
          <Key size={18} />
          <div>
            <strong>如何获取授权码？</strong>
            <p>
              {provider === 'qq'
                ? '登录 QQ 邮箱网页版 → 设置 → 账号 → 开启 IMAP/SMTP 服务 → 生成授权码。'
                : provider === '163'
                  ? '登录网易邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启 IMAP 服务 → 获取授权码。'
                  : '在邮箱服务商提供的设置中开启 IMAP，并按其要求配置客户端凭据。'}
            </p>
          </div>
        </div>
      </div>
      <div className="form-actions">
        <Button type="submit" disabled={busy || folderJob.busy}>
          {busy ? '保存中' : '保存并测试连接'}
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>
          取消
        </Button>
      </div>
    </form>
  );
}

export function AccountsPanel() {
  const cache = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => api<Account[]>('/accounts'),
  });
  const [editor, setEditor] = useState<Account | 'new' | null>(null);
  const [removalTarget, setRemovalTarget] = useState<Account | null>(null);
  const [removing, setRemoving] = useState(false);
  const [removalError, setRemovalError] = useState('');
  const [removalNotice, setRemovalNotice] = useState('');
  const job = useJob('account-test');
  async function toggle(account: Account) {
    try {
      await put(`/accounts/${account.id}`, {
        ...account,
        enabled: !account.enabled,
        expected_revision: account.revision,
      });
      cache.invalidateQueries({ queryKey: ['accounts'] });
    } catch (e) {
      job.setError((e as Error).message);
    }
  }
  async function removeConnection() {
    if (!removalTarget || removing) return;
    const target = removalTarget;
    setRemoving(true);
    setRemovalError('');
    try {
      const result = await api<{ removed: boolean; warning: string | null }>(
        `/accounts/${target.id}?expected_revision=${target.revision}`,
        { method: 'DELETE' },
      );
      setRemovalTarget(null);
      setRemovalNotice(result.warning || '连接已移除，历史邮件、摘要和来源仍保留。');
      await cache.invalidateQueries();
    } catch (e) {
      setRemovalError((e as Error).message);
      const latest = await api<Account[]>('/accounts').catch(() => null);
      if (latest) {
        cache.setQueryData(['accounts'], latest);
        setRemovalTarget(latest.find((account) => account.id === target.id) || null);
      }
    } finally {
      setRemoving(false);
    }
  }
  return (
    <div className="settings-card">
      <div className="section-heading">
        <div>
          <h2>邮箱账号</h2>
          <p className="muted">一个来源一个连接，可以添加多个 QQ 或网易邮箱。</p>
        </div>
        <Button variant="outline" onClick={() => setEditor('new')}>
          <Plus />
          添加邮箱
        </Button>
      </div>
      <ErrorNotice>{error?.message || job.error}</ErrorNotice>
      {removalNotice && (
        <p className="notice" role="status">
          {removalNotice}
        </p>
      )}
      <JobNotice busy={job.busy} phase={job.job?.phase} />
      {!data ? (
        <Loading />
      ) : data.length ? (
        data.map((account) => (
          <div className="account-card" key={account.id}>
            <div className={`provider-mark ${account.provider}`}>
              {account.provider === 'qq' ? 'Q' : account.provider === '163' ? '163' : 'M'}
            </div>
            <div className="account-info">
              <strong>{account.label}</strong>
              <p>{account.email}</p>
              <span>
                {account.host} · {account.folder} · {relative(account.last_sync_at)}
              </span>
              {account.error && <p className="inline-error">{account.error}</p>}
            </div>
            <div className="account-actions">
              <Status
                value={
                  account.status === 'removing'
                    ? 'removing'
                    : account.enabled
                      ? account.status
                      : '已停用'
                }
              />
              <div>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={account.status === 'removing'}
                  onClick={() => setEditor(account)}
                >
                  编辑
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={job.busy || !account.enabled || account.status === 'removing'}
                  onClick={() => job.run(() => post<Task>(`/accounts/${account.id}/test`))}
                >
                  测试连接
                </Button>
                <Switch
                  checked={account.enabled}
                  disabled={account.status === 'removing'}
                  onCheckedChange={() => void toggle(account)}
                  aria-label={`${account.enabled ? '停用' : '启用'}${account.label}`}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setRemovalError('');
                    setRemovalTarget(account);
                  }}
                >
                  {account.status === 'removing' ? '重试移除连接' : '移除连接'}
                </Button>
              </div>
            </div>
          </div>
        ))
      ) : (
        <div className="helper-box">
          <EnvelopeSimple size={22} />
          <div>
            <strong>添加你的第一个邮箱</strong>
            <p>先准备邮箱地址和客户端授权码，服务器参数会自动填写。</p>
          </div>
        </div>
      )}
      <Dialog open={!!editor} onOpenChange={(open) => !open && setEditor(null)}>
        <DialogContent className="account-dialog">
          <DialogHeader>
            <DialogTitle>{editor === 'new' ? '添加邮箱' : '编辑邮箱'}</DialogTitle>
            <DialogDescription>通过 IMAP 只读连接你的邮箱。</DialogDescription>
          </DialogHeader>
          {editor && (
            <AccountEditor
              account={editor === 'new' ? undefined : editor}
              onClose={() => setEditor(null)}
              onSave={(account) => {
                cache.invalidateQueries({ queryKey: ['accounts'] });
                void job.run(() => post<Task>(`/accounts/${account.id}/test`));
              }}
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!removalTarget}
        onOpenChange={(open) => {
          if (!open && !removing) setRemovalTarget(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>移除邮箱连接</DialogTitle>
            <DialogDescription>
              将停止使用这个连接并取消它尚未开始的任务。历史邮件、摘要和来源会保留；重新连接时需要添加账号。
            </DialogDescription>
          </DialogHeader>
          <p>
            {removalTarget?.label} · {removalTarget?.email}
          </p>
          <ErrorNotice>{removalError}</ErrorNotice>
          <div className="form-actions">
            <Button disabled={removing} onClick={() => void removeConnection()}>
              {removing ? '正在移除连接' : '确认移除连接'}
            </Button>
            <Button variant="ghost" disabled={removing} onClick={() => setRemovalTarget(null)}>
              取消
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function ScheduleForm({ allowEnable = true }: { allowEnable?: boolean }) {
  const cache = useQueryClient();
  const { data } = useQuery({
    queryKey: ['schedule'],
    queryFn: () => api<Schedule>('/settings/schedule'),
  });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/accounts') });
  const draft = useDraft<Schedule | null>('schedule', null);
  const form = draft.value;
  const setForm = draft.update;
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (data) draft.hydrate(data);
  }, [data]);
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    setError('');
    try {
      await put('/settings/schedule', { ...form, enabled: allowEnable ? form.enabled : false });
      draft.clear();
      cache.invalidateQueries({ queryKey: ['schedule'] });
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!form) return <Loading />;
  return (
    <div className="settings-card">
      <div className="section-heading">
        <div>
          <h2>汇总与同步计划</h2>
          <p className="muted">由后台服务执行，关闭网页后仍然继续。</p>
        </div>
        {allowEnable && (
          <Switch
            aria-label="启用每日汇总"
            checked={form.enabled}
            onCheckedChange={(value) => setForm({ ...form, enabled: value })}
          />
        )}
      </div>
      <ErrorNotice>{error}</ErrorNotice>
      <form className="form-stack" onSubmit={save}>
        <div className="form-grid">
          <Field label="每日汇总时间">
            <Input
              aria-label="每日汇总时间"
              type="time"
              value={form.digest_time}
              onChange={(e) => setForm({ ...form, digest_time: e.target.value })}
            />
          </Field>
          <Field label="时区">
            <select
              className="native-select"
              aria-label="摘要时区"
              value={form.timezone}
              onChange={(e) => setForm({ ...form, timezone: e.target.value })}
            >
              <option value="Asia/Shanghai">北京时间 · Asia/Shanghai</option>
              <option value="UTC">协调世界时 · UTC</option>
              <option value="Asia/Tokyo">东京时间 · Asia/Tokyo</option>
              <option value="America/New_York">纽约时间 · America/New_York</option>
            </select>
          </Field>
        </div>
        <Field label="参与汇总的邮箱" hint="未选择时，使用全部启用的邮箱。">
          <div className="account-checks">
            {accounts.data
              ?.filter((a) => a.enabled)
              .map((a) => (
                <label key={a.id}>
                  <input
                    type="checkbox"
                    checked={form.account_ids?.includes(a.id)}
                    onChange={(e) =>
                      setForm({
                        ...form,
                        account_ids: e.target.checked
                          ? [...(form.account_ids || []), a.id]
                          : form.account_ids?.filter((id) => id !== a.id),
                      })
                    }
                  />
                  <span>{a.label}</span>
                  <small>{a.email}</small>
                </label>
              ))}
          </div>
        </Field>
        <details className="advanced">
          <summary>同步与首次回溯</summary>
          <div className="sync-toggle">
            <div>
              <strong>后台自动同步</strong>
              <p className="field-hint">单独控制邮件同步，关闭每日汇总后仍可继续。</p>
            </div>
            <Switch
              aria-label="后台自动同步"
              checked={form.sync_enabled}
              onCheckedChange={(value) => setForm({ ...form, sync_enabled: value })}
            />
          </div>
          <div className="form-grid">
            <Field label="后台同步间隔（分钟）">
              <Input
                aria-label="后台同步间隔"
                type="number"
                min={1}
                max={1440}
                value={form.sync_minutes}
                onChange={(e) => setForm({ ...form, sync_minutes: Number(e.target.value) })}
              />
            </Field>
            <Field label="初次缓存天数">
              <Input
                aria-label="初次缓存天数"
                type="number"
                min={1}
                max={365}
                value={form.initial_days}
                onChange={(e) => setForm({ ...form, initial_days: Number(e.target.value) })}
              />
            </Field>
          </div>
          <p className="field-hint">首次摘要默认覆盖最近 24 小时。历史缓存用于检索。</p>
        </details>
        <div className="form-actions">
          <Button type="submit">保存计划</Button>
          {saved && (
            <span className="saved">
              <Check />
              计划已保存
            </span>
          )}
        </div>
      </form>
    </div>
  );
}

export function ConnectionsPage() {
  const [params, setParams] = useSearchParams();
  const requested = params.get('tab') || 'accounts';
  const tab = ['accounts', 'model', 'schedule', 'feishu'].includes(requested)
    ? requested
    : 'accounts';
  const setTab = (tab: string) => setParams({ tab });
  return (
    <>
      <PageHeading
        title="连接与设置"
        description="管理信息来源、分析能力，以及每日汇总的方式。"
        actions={
          <Button variant="outline" asChild>
            <Link to="/setup">
              重新打开配置向导 <ArrowRight />
            </Link>
          </Button>
        }
      />
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置分类">
          {[
            { id: 'accounts', label: '邮箱账号', icon: EnvelopeSimple },
            { id: 'model', label: '模型连接', icon: Robot },
            { id: 'schedule', label: '汇总计划', icon: SlidersHorizontal },
            { id: 'feishu', label: '飞书连接', icon: ChatsIcon },
          ].map((item) => (
            <button
              key={item.id}
              className={tab === item.id ? 'active' : ''}
              onClick={() => setTab(item.id)}
            >
              <item.icon size={18} />
              {item.label}
            </button>
          ))}
        </nav>
        <section>
          {tab === 'accounts' ? (
            <AccountsPanel />
          ) : tab === 'model' ? (
            <ModelForm />
          ) : tab === 'schedule' ? (
            <ScheduleForm />
          ) : (
            <FeishuPanel />
          )}
        </section>
      </div>
    </>
  );
}
function ChatsIcon({ size = 18 }: { size?: number }) {
  return <Robot size={size} />;
}
