import { useState, type FormEvent } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { NavLink, Route, Routes, Link, useLocation } from 'react-router-dom';
import {
  ArrowRight,
  ArrowSquareOut,
  ChatsCircle,
  DeviceMobile,
  EnvelopeSimple,
  GearSix,
  House,
  List,
  ListChecks,
  Monitor,
  PlayCircle,
  SignIn,
  SignOut,
  Sparkle,
  X,
} from '@phosphor-icons/react';
import { api, post, type Auth } from '@/api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ErrorNotice, Loading } from '@/components/shared';
import { OverviewPage, MailsPage, DigestsPage, ChatPage, TasksPage } from '@/pages/workbench';
import { ConnectionsPage, Field } from '@/pages/connections';
import SetupPage from '@/pages/setup';

const nav = [
  { to: '/', label: '概览', icon: House },
  { to: '/mail', label: '邮件', icon: EnvelopeSimple },
  { to: '/digests', label: '来信摘要', icon: ListChecks },
  { to: '/chat', label: '智能查询', icon: ChatsCircle },
  { to: '/connections', label: '连接与设置', icon: GearSix },
  { to: '/tasks', label: '任务记录', icon: List },
];
const promoVideo = new URL('./assets/laixin-promo.mp4', import.meta.url).href;
const promoPoster = new URL('./assets/laixin-promo-poster.jpg', import.meta.url).href;
const promoCaptions = new URL('./assets/laixin-promo.vtt', import.meta.url).href;

function Brand() {
  return (
    <Link to="/" className="brand">
      <span>
        <EnvelopeSimple weight="bold" size={22} />
      </span>
      <div>
        <strong>来信</strong>
        <small>MAIL WORKSPACE</small>
      </div>
    </Link>
  );
}
function Login({ auth }: { auth: Auth }) {
  const cache = useQueryClient();
  const [password, setPassword] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const demo = auth.mode === 'demo';

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError('');
    try {
      await post(
        demo ? '/demo/start' : auth.initialized ? '/auth/login' : '/auth/bootstrap',
        demo ? {} : auth.initialized ? { password } : { password, token },
      );
      cache.invalidateQueries();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <a className="login-skip" href="#login-main">
        跳到主要内容
      </a>
      <header className="login-header">
        <Brand />
        <a className="login-preview-link" href="#login-preview">
          <PlayCircle size={19} aria-hidden="true" />
          产品介绍
        </a>
      </header>
      <main className="login-layout" id="login-main" tabIndex={-1}>
        <section className="login-copy">
          <h1>
            来信纷至，
            <br />
            <span>要事有序。</span>
          </h1>
          <p>从多个邮箱提炼优先事项与截止时间。打开一份摘要，就知道先处理什么。</p>
        </section>
        <section className="login-form" aria-labelledby="login-form-title">
          <div className="login-form-heading">
            <span>
              {demo ? (
                <Sparkle size={20} aria-hidden="true" />
              ) : (
                <SignIn size={20} aria-hidden="true" />
              )}
            </span>
            <h2 id="login-form-title">
              {demo ? '体验演示工作台' : auth.initialized ? '登录工作台' : '建立你的工作台'}
            </h2>
          </div>
          <p className="login-form-intro">
            {demo
              ? '用六封样例邮件体验摘要和连续查询。模型回答由规则模拟。'
              : auth.initialized
                ? '输入管理密码，查看你的来信。'
                : '设置管理密码，然后按向导连接模型与邮箱。'}
          </p>
          <ErrorNotice>{error}</ErrorNotice>
          {demo ? (
            <Button className="full-width" onClick={() => submit()} disabled={busy}>
              {busy ? '正在准备工作台' : '进入演示工作台'}
              <ArrowRight aria-hidden="true" />
            </Button>
          ) : (
            <form className="form-stack" onSubmit={submit}>
              {!auth.initialized && (
                <Field
                  label="一次性初始化凭证"
                  hint="从部署端 bootstrap-token 命令取得，仅首次初始化需要。"
                >
                  <Input
                    aria-label="初始化凭证"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    required
                  />
                </Field>
              )}
              <Field
                label={auth.initialized ? '管理密码' : '设置管理密码'}
                hint={auth.initialized ? undefined : '至少 8 个字符。'}
              >
                <Input
                  aria-label="管理密码"
                  type="password"
                  autoComplete={auth.initialized ? 'current-password' : 'new-password'}
                  value={password}
                  minLength={auth.initialized ? 1 : 8}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>
              <Button type="submit" className="full-width" disabled={busy}>
                {busy ? '正在打开' : auth.initialized ? '进入工作台' : '初始化并开始配置'}
                <ArrowRight aria-hidden="true" />
              </Button>
            </form>
          )}
        </section>
        <section
          className="login-preview"
          id="login-preview"
          aria-labelledby="login-promo-title"
          tabIndex={-1}
        >
          <figure className="login-promo">
            <video
              aria-labelledby="login-promo-title"
              controls
              playsInline
              preload="none"
              poster={promoPoster}
              width={1920}
              height={1080}
            >
              <source src={promoVideo} type="video/mp4" />
              <track kind="captions" label="简体中文" srcLang="zh-CN" src={promoCaptions} />
              <a href={promoVideo}>打开产品介绍视频</a>
            </video>
            <figcaption>
              <h2 id="login-promo-title">来信产品介绍</h2>
              <time dateTime="PT32S" aria-label="32 秒">
                0:32
              </time>
            </figcaption>
          </figure>
          <ul className="login-features">
            <li>
              <Monitor size={22} aria-hidden="true" />
              <div>
                <h3>网页工作台</h3>
                <p>集中查看多个邮箱，核对邮件原文。</p>
              </div>
            </li>
            <li>
              <DeviceMobile size={22} aria-hidden="true" />
              <div>
                <h3>手机飞书</h3>
                <p>绑定后接收每日摘要，随时追问邮件。</p>
              </div>
            </li>
          </ul>
        </section>
      </main>
      <footer className="login-footer">
        <span>来信 · 多邮箱智能助手</span>
        <span>
          {demo ? '演示数据与真实邮箱、模型及飞书隔离。' : '单用户自托管 · 数据保存在你的服务中'}
        </span>
      </footer>
    </div>
  );
}

export default function App() {
  const cache = useQueryClient();
  const { data: auth, error } = useQuery({
    queryKey: ['auth'],
    queryFn: () => api<Auth>('/auth/status'),
    retry: false,
  });
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => api<{ worker: { alive: boolean } }>('/health'),
    refetchInterval: 5000,
    enabled: !!auth?.authenticated,
  });
  if (error)
    return (
      <div className="connection-error">
        <ErrorNotice>{error.message}。请确认后端服务已启动。</ErrorNotice>
        <Button onClick={() => cache.invalidateQueries()}>重新连接</Button>
      </div>
    );
  if (!auth) return <Loading />;
  if (!auth.authenticated) return <Login auth={auth} />;
  const title =
    location.pathname === '/setup'
      ? '配置向导'
      : nav.find((n) => n.to === location.pathname)?.label || '工作台';
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileOpen ? 'open' : ''}`}>
        <Brand />
        <nav aria-label="主导航">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              aria-label={item.label}
              to={item.to}
              end={item.to === '/'}
              onClick={() => setMobileOpen(false)}
            >
              <item.icon size={19} weight="regular" />
              <span>{item.label}</span>
              {item.to === '/chat' && <span className="nav-small">AI</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <Link to="/setup" className="setup-shortcut">
            <Sparkle size={18} />
            <div>
              <strong>配置向导</strong>
              <small>一步一步，连接好</small>
            </div>
            <ArrowRight size={16} />
          </Link>
          <div className="workspace-identity">
            <span className="avatar">研</span>
            <div>
              <strong>个人工作台</strong>
              <small>SELF-HOSTED</small>
            </div>
            <button
              aria-label="退出登录"
              onClick={async () => {
                await post('/auth/logout');
                cache.clear();
                cache.invalidateQueries();
              }}
            >
              <SignOut size={18} />
            </button>
          </div>
        </div>
      </aside>
      {mobileOpen && (
        <button
          aria-label="关闭导航"
          className="sidebar-overlay"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div>
            <button
              className="mobile-menu"
              aria-label="打开导航"
              onClick={() => setMobileOpen(!mobileOpen)}
            >
              {mobileOpen ? <X /> : <List />}
            </button>
            <span className="breadcrumb">
              个人工作台 <span>/</span> <strong>{title}</strong>
            </span>
          </div>
          <div className="topbar-right">
            <span className={`worker-state ${health.data?.worker.alive ? 'alive' : ''}`}>
              <i />
              {health.data?.worker.alive ? '后台在线' : '后台未连接'}
            </span>
            <span className="topbar-date">
              {new Intl.DateTimeFormat('zh-CN', {
                month: 'long',
                day: 'numeric',
                weekday: 'short',
              }).format(new Date())}
            </span>
          </div>
        </header>
        {auth.mode === 'demo' && (
          <div className="demo-banner">
            <Sparkle size={15} />
            <span>样例工作台 · 邮件与模型均为模拟，仅用于体验流程</span>
          </div>
        )}
        <main
          className={
            location.pathname === '/setup' ? 'page-content wizard-content' : 'page-content'
          }
        >
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/mail" element={<MailsPage />} />
            <Route path="/digests" element={<DigestsPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/connections" element={<ConnectionsPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/setup" element={<SetupPage />} />
            <Route path="*" element={<OverviewPage />} />
          </Routes>
        </main>
        <footer className="workspace-footer">
          <span>来信 · 让邮件成为清楚的行动</span>
          <a href="/docs" target="_blank" rel="noreferrer">
            接口文档 <ArrowSquareOut size={13} />
          </a>
        </footer>
      </div>
    </div>
  );
}
