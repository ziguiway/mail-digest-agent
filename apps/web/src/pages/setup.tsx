import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check, Sparkle } from '@phosphor-icons/react';
import {
  api,
  post,
  put,
  type Account,
  type Digest,
  type ModelProfile,
  type Schedule,
  type Task,
} from '@/api/client';
import { Button } from '@/components/ui/button';
import { ErrorNotice, JobNotice, Loading, Status } from '@/components/shared';
import { AccountsPanel, FeishuPanel, ModelForm, ScheduleForm } from './connections';
import { useJob } from '@/hooks/use-job';

const steps = [
  {
    title: '连接模型',
    description: '让助手具备分析和检索能力',
    tip: '选择兼容的模型服务',
    body: '服务地址、模型名称和 API Key 通常可以在服务商控制台找到。测试会分别验证结构化摘要与工具调用。',
  },
  {
    title: '添加邮箱',
    description: '把分散的来信连接在一起',
    tip: '准备客户端授权码',
    body: '在邮箱网页版开启 IMAP 服务，获取客户端授权码。同一个服务商可以添加多个账号。',
  },
  {
    title: '设置汇总',
    description: '选择每天看摘要的时间',
    tip: '后台执行，随时可改',
    body: '汇总时间按所选时区计算。首次缓存默认回溯 30 天，方便查询历史；首份摘要只覆盖最近 24 小时。',
  },
  {
    title: '生成首份摘要',
    description: '用一次试运行确认整个流程',
    tip: '每一步都能看见',
    body: '先同步邮件，再识别事项、截止时间与优先级。你可以核对每项摘要的原始邮件依据。',
  },
  {
    title: '连接飞书',
    description: '让摘要在常用的地方送达',
    tip: '可以稍后再配置',
    body: '网页已经可以完成日常使用。飞书用于主动推送和移动端查询，可以在连接与设置中继续配置。',
  },
];
export default function SetupPage() {
  const cache = useQueryClient();
  const navigate = useNavigate();
  const job = useJob();
  const [localStep, setLocalStep] = useState<number | null>(null);
  const [error, setError] = useState('');
  const state = useQuery({
    queryKey: ['onboarding'],
    queryFn: () => api<{ step: number; complete: boolean }>('/onboarding'),
  });
  const model = useQuery({
    queryKey: ['model'],
    queryFn: () => api<ModelProfile>('/settings/model'),
  });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/accounts') });
  const digests = useQuery({ queryKey: ['digests'], queryFn: () => api<Digest[]>('/digests') });
  if (!state.data) return <Loading />;
  const step = Math.min(5, localStep ?? state.data.step);
  const current = steps[step - 1];
  const ready =
    step === 1
      ? model.data?.status === 'verified'
      : step === 2
        ? accounts.data?.some((a) => a.enabled && a.status === 'verified')
        : step === 4
          ? !!digests.data?.length
          : true;
  async function go(next: number) {
    setError('');
    try {
      await put('/onboarding', { step: next, complete: false });
      setLocalStep(next);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function finish(enable: boolean) {
    try {
      if (enable) {
        const schedule = await api<Schedule>('/settings/schedule');
        await put('/settings/schedule', { ...schedule, enabled: true });
      }
      await put('/onboarding', { step: 6, complete: true });
      cache.invalidateQueries();
      navigate('/');
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="setup-layout">
      <aside className="setup-sidebar">
        <p className="eyebrow">GET STARTED</p>
        <h1>把来信，安排好。</h1>
        <p>几步配置，让邮件成为清楚的行动。</p>
        <ol>
          {steps.map((item, index) => (
            <li
              key={item.title}
              className={step === index + 1 ? 'active' : step > index + 1 ? 'complete' : ''}
            >
              <span>
                {step > index + 1 ? <Check size={16} /> : String(index + 1).padStart(2, '0')}
              </span>
              <div>
                <strong>{item.title}</strong>
                <p>{item.description}</p>
              </div>
            </li>
          ))}
        </ol>
        <div className="setup-help">
          <strong>每一步保存后都能继续</strong>
          <p>已保存的配置会保留，刷新页面后可以接着完成。</p>
        </div>
      </aside>
      <main className="setup-main">
        <div className="setup-heading">
          <p className="eyebrow">STEP {String(step).padStart(2, '0')} / 05</p>
          <h2>{current.title}</h2>
          <p>{current.description}</p>
        </div>
        <ErrorNotice>{error || job.error}</ErrorNotice>
        {step === 1 ? (
          <ModelForm />
        ) : step === 2 ? (
          <AccountsPanel />
        ) : step === 3 ? (
          <ScheduleForm allowEnable={false} />
        ) : step === 4 ? (
          <section className="settings-card">
            <div className="trial-illustration">
              <Sparkle size={36} />
            </div>
            <h2>看看第一份摘要</h2>
            <p className="muted">
              从已连接邮箱读取邮件，提取事项与处理优先级。此步骤只生成网页摘要。
            </p>
            <JobNotice busy={job.busy} phase={job.job?.phase} />
            <Button disabled={job.busy} onClick={() => job.run(() => post<Task>('/digests'))}>
              <Sparkle />
              开始试运行
            </Button>
            {digests.data?.[0] && (
              <div className="trial-result">
                <Status value={digests.data[0].status} />
                <h3>{digests.data[0].title}</h3>
                <p>已生成 {digests.data[0].items.length} 件邮件事项，进入工作台后可以逐项核对。</p>
              </div>
            )}
          </section>
        ) : (
          <FeishuPanel />
        )}
        <div className="wizard-actions">
          <Button variant="ghost" disabled={step === 1} onClick={() => go(step - 1)}>
            <ArrowLeft />
            上一步
          </Button>
          {step < 5 ? (
            <Button disabled={!ready} onClick={() => go(step + 1)}>
              下一步 <ArrowRight />
            </Button>
          ) : (
            <div className="heading-actions">
              <Button variant="outline" onClick={() => finish(false)}>
                先进入工作台
              </Button>
              <Button onClick={() => finish(true)}>
                启用每日汇总并完成 <ArrowRight />
              </Button>
            </div>
          )}
        </div>
        {!ready && (
          <p className="field-hint">
            {step === 1
              ? '保存并验证模型后继续。'
              : step === 2
                ? '至少一个邮箱通过连接测试后继续。'
                : '生成首份摘要后继续。'}
          </p>
        )}
      </main>
      <aside className="setup-note">
        <span className="note-rule" />
        <h3>{current.tip}</h3>
        <p>{current.body}</p>
      </aside>
    </div>
  );
}
