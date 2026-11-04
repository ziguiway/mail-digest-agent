import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowSquareOut, Copy, LinkSimple, PaperPlaneTilt, QrCode } from '@phosphor-icons/react';
import { api, post, put, type Task } from '@/api/client';
import type { components } from '@/api/schema';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { ErrorNotice, JobNotice, Loading, Status } from '@/components/shared';
import { useJob } from '@/hooks/use-job';
import { time } from '@/lib/utils';
import { DeliveryHistory } from '@/components/delivery-history';

export type FeishuState = components['schemas']['FeishuView'];
type Listener = { ready: boolean; error?: string; warning?: string; missing_scopes?: string[] };
type Url = { url: string; qr_id: string };

export function FeishuPanel() {
  const cache = useQueryClient();
  const query = useQuery({
    queryKey: ['feishu'],
    queryFn: () => api<FeishuState>('/settings/feishu'),
    refetchInterval: 1500,
  });
  const data = query.data;
  const authorization = useJob('feishu-authorization');
  const test = useJob('feishu-test');
  const [mode, setMode] = useState<'new' | 'existing'>('new');
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const listener = (data?.listener || {}) as Listener;
  async function act(operation: () => Promise<FeishuState>) {
    setBusy(true);
    setError('');
    try {
      const value = await operation();
      cache.setQueryData(['feishu'], value);
      cache.invalidateQueries({ queryKey: ['deliveries'] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function bind(event: FormEvent) {
    event.preventDefault();
    const task = await authorization.run(() =>
      post<Task>('/settings/feishu/bind', {
        mode,
        previous_binding_id: data?.binding_id || '',
        app_id: mode === 'existing' ? appId : '',
        app_secret: mode === 'existing' ? appSecret : null,
      }),
    );
    if (task) {
      setAppSecret('');
      cache.invalidateQueries({ queryKey: ['feishu'] });
    }
  }
  if (!data) return query.error ? <ErrorNotice>{query.error.message}</ErrorNotice> : <Loading />;
  const awaiting = data.status === 'authorizing';
  const disabled = !!data.simulated || busy;
  return (
    <div className="feishu-settings">
      <section className="settings-card">
        <div className="section-heading">
          <div>
            <h2>飞书连接</h2>
            <p className="muted">以应用机器人身份，把摘要和邮件查询带到你的单聊。</p>
          </div>
          <Status value={data.status} />
        </div>
        <ErrorNotice>
          {error ||
            authorization.error ||
            test.error ||
            data.error ||
            listener.error ||
            query.error?.message}
        </ErrorNotice>
        {data.simulated && (
          <div className="notice warning">
            当前为样例模式，绑定和发送操作已关闭。启动真实配置环境后，可以在这里完成同样的引导。
          </div>
        )}
        <JobNotice busy={authorization.busy && awaiting} phase={authorization.job?.phase} />
        {!data.configured && (
          <form className="form-stack" onSubmit={bind}>
            <h3 className="step-title">
              <span>01</span>连接一个应用
            </h3>
            <div className="binding-options">
              <label>
                <input
                  type="radio"
                  name="binding-mode"
                  value="new"
                  checked={mode === 'new'}
                  disabled={awaiting || disabled}
                  onChange={() => setMode('new')}
                />
                创建新应用
              </label>
              <label>
                <input
                  type="radio"
                  name="binding-mode"
                  value="existing"
                  checked={mode === 'existing'}
                  disabled={awaiting || disabled}
                  onChange={() => setMode('existing')}
                />
                绑定已有应用
              </label>
            </div>
            {mode === 'existing' && (
              <div className="form-grid">
                <label className="form-field">
                  App ID
                  <Input
                    aria-label="飞书 App ID"
                    value={appId}
                    onChange={(e) => setAppId(e.target.value)}
                    required
                    disabled={awaiting}
                  />
                </label>
                <label className="form-field">
                  App Secret
                  <Input
                    aria-label="飞书 App Secret"
                    type="password"
                    autoComplete="new-password"
                    value={appSecret}
                    onChange={(e) => setAppSecret(e.target.value)}
                    required
                    disabled={awaiting}
                  />
                </label>
              </div>
            )}
            <p className="field-hint">
              {mode === 'new'
                ? '点击后会生成飞书原始授权链接及二维码，请在自己的飞书中完成应用配置。'
                : '从自建应用的凭证页面取得 App ID 和 App Secret，保存后继续检查机器人和事件配置。'}
            </p>
            <div className="form-actions">
              <Button type="submit" disabled={disabled || awaiting || authorization.busy}>
                <LinkSimple />
                {awaiting
                  ? '正在等待授权'
                  : mode === 'new'
                    ? '创建专用应用并开始绑定'
                    : '保存并绑定已有应用'}
              </Button>
              {awaiting && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={busy}
                  onClick={() => act(() => post('/settings/feishu/disconnect'))}
                >
                  取消本次绑定
                </Button>
              )}
              {data.auth_task_id && ['error', 'expired'].includes(data.status) && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={disabled || authorization.busy}
                  onClick={() =>
                    authorization.run(() => post<Task>(`/tasks/${data.auth_task_id}/retry`))
                  }
                >
                  重试本次绑定
                </Button>
              )}
            </div>
          </form>
        )}
        {!!data.urls?.length && (
          <div className="authorization-links">
            {((data.urls || []) as unknown as Url[]).map((item) => (
              <div className="authorization-link" key={item.qr_id}>
                <div>
                  <strong>{data.configured ? '飞书权限配置页面' : '在飞书中完成授权'}</strong>
                  <a href={item.url} target="_blank" rel="noreferrer" className="raw-auth-link">
                    {item.url}
                    <ArrowSquareOut size={16} />
                  </a>
                  {data.authorization_expires_at && (
                    <p className="field-hint">请尽快完成授权，链接有效期以飞书页面为准。</p>
                  )}
                </div>
                <img
                  src={`/api/v1/settings/feishu/qr/${item.qr_id}`}
                  alt="飞书授权或配置二维码"
                  width={180}
                  height={180}
                />
              </div>
            ))}
          </div>
        )}
        {data.configured && (
          <>
            <div className="bound-app">
              <span>
                <LinkSimple size={18} />
                已保存应用
              </span>
              <code>{data.app_id}</code>
            </div>
            <h3 className="step-title">
              <span>02</span>检查机器人与消息通道
            </h3>
            <ol className="permission-guide">
              <li>在开发者后台开启机器人能力，把自己加入应用可用范围。</li>
              <li>
                开通机器人发送消息与读取单聊消息的权限：<code>im:message:send_as_bot</code>、
                <code>im:message.p2p_msg:readonly</code>。
              </li>
              <li>
                在事件订阅中使用长连接接收方式，添加 <code>im.message.receive_v1</code> 事件。
              </li>
              <li>完成应用发布，再回到这里检查通道状态。</li>
            </ol>
            {!!listener.missing_scopes?.length && (
              <div className="notice warning">尚缺权限：{listener.missing_scopes.join('、')}</div>
            )}
            <div className="channel-check">
              <Status value={listener.ready ? 'ready' : 'connecting'} />
              <Button
                variant="outline"
                disabled={disabled}
                onClick={() => act(() => post('/settings/feishu/reconnect'))}
              >
                重新检查消息通道
              </Button>
            </div>
            {listener.warning && <div className="notice warning">{listener.warning}</div>}
            <h3 className="step-title">
              <span>03</span>配对你的飞书单聊
            </h3>
            <p className="muted">
              打开与这个机器人的单聊，发送下方配对码。收到后会自动识别你的会话。
            </p>
            <div className="form-actions">
              <Button
                variant="outline"
                disabled={disabled || !listener.ready}
                onClick={() => act(() => post('/settings/feishu/pair'))}
              >
                <QrCode />
                {data.paired_at ? '重新生成配对码' : '生成配对码'}
              </Button>
            </div>
            {data.pairing_code && (
              <div className="pairing-box">
                <div>
                  <span>发送这条消息给机器人</span>
                  <code>{data.pairing_code}</code>
                  <p className="field-hint">
                    有效期至 {time(data.pairing_expires_at)}，仅用于绑定这一个单聊。
                  </p>
                </div>
                <Button
                  variant="ghost"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(data.pairing_code!);
                      setCopied(true);
                    } catch {
                      setError('无法自动复制，请手动选中配对码复制。');
                    }
                  }}
                >
                  <Copy />
                  {copied ? '已复制' : '复制'}
                </Button>
              </div>
            )}
            {data.status === 'pairing' && !data.pairing_code && (
              <p className="field-hint">配对码已过期，请重新生成。</p>
            )}
            {data.paired_at && (
              <div className="paired-target">
                <strong>已配对的接收对象</strong>
                <span>
                  用户：<code>{data.owner_open_id}</code>
                </span>
                <span>
                  单聊：<code>{data.chat_id}</code>
                </span>
              </div>
            )}
            <h3 className="step-title">
              <span>04</span>发送测试并启用
            </h3>
            <p className="field-hint">
              下面的测试以应用机器人身份，发送到上方已配对的单聊。消息内容如下：
            </p>
            <blockquote className="test-message">{data.test_message}</blockquote>
            <JobNotice busy={test.busy} phase={test.job?.phase} />
            <Button
              variant="outline"
              disabled={disabled || test.busy || !data.paired_at || !listener.ready}
              onClick={() =>
                test.run(() => post<Task>('/settings/feishu/test', { binding_id: data.binding_id }))
              }
            >
              <PaperPlaneTilt />
              以机器人身份发送测试消息
            </Button>
            {data.last_test_at && (
              <p className="saved">最近发送测试通过：{time(data.last_test_at)}</p>
            )}
            <div className="enable-feishu">
              <div>
                <strong>启用飞书收发与摘要推送</strong>
                <p className="field-hint">
                  开启后，配对用户的邮件问题由机器人回答；每日摘要按照汇总计划推送。
                </p>
              </div>
              <Switch
                aria-label="启用飞书收发与摘要推送"
                checked={data.enabled}
                disabled={disabled || !data.paired_at || !data.last_test_at || !listener.ready}
                onCheckedChange={(enabled) =>
                  act(() => put('/settings/feishu', { enabled, binding_id: data.binding_id }))
                }
              />
            </div>
            {data.enabled && (
              <div className="helper-box">
                <div>
                  <strong>现在可以在飞书里使用</strong>
                  <p>
                    直接询问邮件事项，或发送“现在汇总一下”。发送 /new
                    开始新的对话，网页中的查询和摘要仍可随时查看。
                  </p>
                </div>
              </div>
            )}
            <div className="form-actions">
              <Button
                variant="ghost"
                disabled={busy}
                onClick={() => act(() => post('/settings/feishu/disconnect'))}
              >
                解除飞书绑定
              </Button>
              <span className="field-hint">解除后停止此连接的监听与后续发送。</span>
            </div>
          </>
        )}
      </section>
      <DeliveryHistory />
    </div>
  );
}
