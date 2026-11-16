import type { CSSProperties, ReactNode } from 'react';
import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from 'remotion';

export const paper = '#f7f6f3';
const ink = '#2b3628';
const muted = '#50604a';
const glass = '#f1f3ed';
const white = '#fffefb';
const blue = '#28586d';
const font = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif';
const quick = Easing.bezier(.33, 0, .15, 1);
const press = Easing.bezier(.2, 1.22, .38, 1);
const radius = 22;
const border = '1px solid #dbe2d6';

export type PhoneSceneProps = {
  title: string;
  subtitle: string;
  accent: string;
  label: string;
};

function ramp(frame: number, from: number, to: number, easing = quick) {
  return interpolate(frame, [from, to], [0, 1], {
    easing, extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
  });
}

function enter(frame: number, start: number, duration = 20): CSSProperties {
  const progress = ramp(frame, start, start + duration);
  return { opacity: progress, transform: `translateY(${20 * (1 - progress)}px)` };
}

function Mark({ width = 58, tint = '#e9f0e0' }: { width?: number; tint?: string }) {
  return (
    <div style={{ width, height: width, position: 'relative', display: 'grid', placeItems: 'center',
      flexShrink: 0, borderRadius: width * .24, background: tint, color: '#354930' }}>
      <div style={{ width: width * .55, height: width * .37, overflow: 'hidden', position: 'relative',
        borderRadius: width * .055, border: `${Math.max(2, width * .044)}px solid currentColor` }}>
        <div style={{ width: '71%', aspectRatio: '1', position: 'absolute', left: '50%', top: '-70%',
          transform: 'translateX(-50%) rotate(45deg)', borderRight: '2.6px solid currentColor',
          borderBottom: '2.6px solid currentColor', borderRadius: '0 0 3px 0' }} />
      </div>
    </div>
  );
}

function Stage({ title, subtitle, accent, label, children, dark = false, detail, demo = '飞书流程示意 · 样例邮件', copyWidth = 930, titleSize = 99 }:
  PhoneSceneProps & { children: ReactNode; dark?: boolean; detail?: ReactNode; demo?: string; copyWidth?: number; titleSize?: number }) {
  const frame = useCurrentFrame();
  const copy = ramp(frame, 0, 23);
  const foreground = dark ? paper : ink;
  const soft = dark ? '#dce8d2' : muted;
  const rule = dark ? '#dce8d233' : '#c8d2c1';
  return (
    <AbsoluteFill style={{ fontFamily: font, color: foreground,
      background: dark
        ? `radial-gradient(ellipse at 75% 57%, ${accent} 0%, #283226 66%, #20271f 100%)`
        : 'radial-gradient(ellipse at 76% 52%, #e2ebdd 0%, #f7f6f3 64%, #f5f3ee 100%)',
      overflow: 'hidden' }}>
      <div style={{ position: 'absolute', left: 126, right: 126, top: 47, height: 48, zIndex: 3,
        borderBottom: `1px solid ${rule}`, color: soft, display: 'flex', alignItems: 'start',
        justifyContent: 'space-between', fontSize: 25, fontWeight: 650, letterSpacing: 2 }}>
        <span>来信</span>
        <span>{label}</span>
      </div>
      <div style={{ position: 'absolute', top: 298, left: 126, width: copyWidth, color: foreground,
        opacity: .35 + .65 * copy, transform: `translateY(${16 * (1 - copy)}px)`, zIndex: 2 }}>
        <h1 style={{ margin: 0, whiteSpace: 'pre-line', fontSize: titleSize, letterSpacing: -4,
          fontWeight: 720, lineHeight: 1.16 }}>{title}</h1>
        <p style={{ margin: '35px 0 0', color: soft, whiteSpace: 'pre-line', fontSize: 40,
          fontWeight: 480, letterSpacing: -.6, lineHeight: 1.54 }}>{subtitle}</p>
        {detail}
      </div>
      <div style={{ position: 'absolute', left: 126, right: 126, bottom: 62, paddingTop: 17,
        borderTop: `1px solid ${rule}`, color: soft, fontSize: 23, letterSpacing: 1.6 }}>
        {demo}
      </div>
      {children}
    </AbsoluteFill>
  );
}

function Phone({ children, style, background = glass, status = '08:30' }:
  { children: ReactNode; style?: CSSProperties; background?: string; status?: string }) {
  return (
    <div style={{ position: 'absolute', left: 1122, top: 89, width: 610, height: 903,
      background: 'linear-gradient(146deg, #818e78, #253024 24%, #374432 82%, #727f69)',
      border: '2px solid #7c8a73', borderRadius: 76, padding: 12, boxSizing: 'border-box',
      boxShadow: '0 7px 12px #151a167a, 0 44px 80px -26px #29332690, 0 86px 120px -42px #29332678', ...style }}>
      <div style={{ position: 'absolute', left: -6, top: 188, width: 6, height: 94, borderRadius: 5,
        background: '#788573' }} />
      <div style={{ position: 'absolute', right: -6, top: 247, width: 6, height: 78, borderRadius: 5,
        background: '#788573' }} />
      <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden',
        border: '5px solid #1e281f', borderRadius: 62, boxSizing: 'border-box', background, color: ink }}>
        <div style={{ position: 'absolute', top: 13, left: 38, right: 39, height: 36, display: 'flex',
          justifyContent: 'space-between', alignItems: 'center', fontSize: 23, fontWeight: 680, zIndex: 4 }}>
          <span>{status}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ display: 'flex', alignItems: 'end', gap: 3 }}>
              {[7, 11, 15, 19].map(height => <i key={height} style={{ width: 4, height, borderRadius: 2, background: ink }} />)}
            </span>
            <span style={{ width: 23, height: 12, border: `2px solid ${ink}`, borderRadius: 4,
              boxShadow: 'inset 0 0 0 2px #f7f6f3', background: ink }} />
          </div>
        </div>
        <div style={{ position: 'absolute', top: 13, left: '50%', width: 103, height: 28,
          transform: 'translateX(-50%)', background: '#232c23', borderRadius: 26, zIndex: 4 }} />
        <div style={{ position: 'absolute', inset: '57px 0 27px', overflow: 'hidden' }}>{children}</div>
        <div style={{ position: 'absolute', left: '50%', bottom: 11, width: 160, height: 6,
          transform: 'translateX(-50%)', background: '#283129', borderRadius: 10 }} />
      </div>
    </div>
  );
}

function ChatHeader() {
  return (
    <div style={{ height: 86, padding: '0 28px', display: 'flex', alignItems: 'center',
      gap: 14, borderBottom: border, background: white }}>
      <Mark width={51} />
      <div style={{ lineHeight: 1.1 }}>
        <strong style={{ fontSize: 34 }}>来信助手</strong>
        <div style={{ fontSize: 22, color: muted, marginTop: 6 }}>飞书单聊</div>
      </div>
    </div>
  );
}

type MailRow = { title: string; sender: string; body: string };
const qqMail: MailRow[] = [
  { title: '课程项目阶段材料提交', sender: '课程助教', body: '请上传项目说明。截止：明天 18:00。' },
  { title: '本周人工智能研究简报', sender: '研究生院资讯', body: '本周公开讲座和课程资料。' },
  { title: '校园邮箱服务通知', sender: '信息化办公室', body: '本周六凌晨例行维护。' },
];
const neteaseMail: MailRow[] = [
  { title: '课题组会议通知', sender: '课题组导师', body: '原定周五 14:00，地点 A301。' },
  { title: '课题组会议时间更新', sender: '课题组导师', body: '调整为周五 16:00，地点 B202。' },
  { title: '实验室设备借用确认', sender: '实验室管理员', body: '请确认预计归还日期。' },
];

function Inbox({ name, messages, selected, progress }: {
  name: string; messages: MailRow[]; selected: number; progress: number;
}) {
  return (
    <div style={{ padding: '21px 27px', background: '#f9f8f3', height: '100%', boxSizing: 'border-box' }}>
      <div style={{ color: muted, fontSize: 27, fontWeight: 620 }}>{name}</div>
      <div style={{ fontSize: 58, lineHeight: 1.13, margin: '12px 0 24px', letterSpacing: -2, fontWeight: 720 }}>收件箱</div>
      <div style={{ display: 'grid', gap: 12 }}>
        {messages.map(({ title, sender }, i) => (
          <div key={title} style={{ background: i === selected ? '#e8eedf' : white,
            border, borderRadius: radius, padding: '18px 21px', opacity: i === selected ? 1 : .74 }}>
            <div style={{ fontSize: 23, color: muted, marginBottom: 6 }}>{sender}</div>
            <div style={{ fontSize: 31, fontWeight: 650 }}>{title}</div>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 21, padding: '19px 23px 24px', borderRadius: radius,
        background: '#344632', color: paper, opacity: progress,
        transform: `translateY(${25 * (1 - progress)}px)` }}>
        <div style={{ fontSize: 23, color: '#dde9d5' }}>已打开 · {messages[selected].title}</div>
        <div style={{ marginTop: 9, fontSize: 28, lineHeight: 1.4 }}>{messages[selected].body}</div>
      </div>
    </div>
  );
}

export function FrictionScene(props: PhoneSceneProps) {
  const frame = useCurrentFrame();
  const switchMail = ramp(frame, 53, 79);
  const oldVisible = frame < 115;
  const selected = oldVisible ? 0 : 1;
  return (
    <Stage {...props} dark demo="邮箱操作示意 · 样例邮件">
      <Phone status="08:27" style={{ transform: `rotate(${-6 + 12 * switchMail}deg)` }}>
        <div style={{ position: 'absolute', inset: 0, opacity: 1 - switchMail,
          transform: `translateX(${-220 * switchMail}px)` }}>
          <Inbox name="QQ 邮箱" messages={qqMail} selected={0} progress={ramp(frame, 27, 42)} />
        </div>
        <div style={{ position: 'absolute', inset: 0, opacity: switchMail,
          transform: `translateX(${260 * (1 - switchMail)}px)` }}>
          <Inbox name="网易邮箱" messages={neteaseMail} selected={selected}
            progress={oldVisible ? ramp(frame, 87, 101) : ramp(frame, 115, 128)} />
        </div>
      </Phone>
    </Stage>
  );
}

function Ripple({ frame }: { frame: number }) {
  const rings = [
    [1840, '#dee8d7'], [1470, '#e4ecdf'], [1110, '#ebf0e6'], [790, '#f2f4ee'],
  ] as const;
  return (
    <div style={{ position: 'absolute', left: 1427, top: 552, pointerEvents: 'none' }}>
      {rings.map(([size, background], i) => {
        const wave = Math.sin((frame / 180) * Math.PI * 3 - 1.7 * i);
        return (
          <div key={size} style={{ position: 'absolute', width: size, height: size, background, borderRadius: '50%',
            left: -size / 2, top: -size / 2, border: '1px solid #fff7',
            transform: `scale(${1 + .035 * wave})`, boxSizing: 'border-box' }} />
        );
      })}
    </div>
  );
}

export function ArrivalScene(props: PhoneSceneProps) {
  const frame = useCurrentFrame();
  const rise = ramp(frame, 0, 26);
  const notification = ramp(frame, 29, 52, press);
  const light = ramp(frame, 32, 64);
  const open = ramp(frame, 94, 118);
  return (
    <Stage {...props}>
      <Ripple frame={frame} />
      <Phone status="08:30" background="linear-gradient(130deg, #d8e5ce, #edf1e7 66%, #c9d9c0)"
        style={{ transform: `translateY(${83 * (1 - rise)}px)`, opacity: .25 + .75 * rise }}>
        <div style={{ padding: '76px 31px 0', textAlign: 'center' }}>
          <div style={{ fontSize: 26, color: '#41543e', fontWeight: 650 }}>每日汇总</div>
          <div style={{ fontSize: 126, fontWeight: 650, letterSpacing: -6,
            fontVariantNumeric: 'tabular-nums', lineHeight: 1.16 }}>08:30</div>
          <div style={{ marginTop: 57, padding: 26, textAlign: 'left', background: white, borderRadius: 29,
            boxShadow: '0 25px 50px -24px #2c40287a', border, opacity: light,
            transform: `translateY(${-65 * (1 - notification)}px) scale(${.91 + .09 * notification})` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 27, fontWeight: 670,
              color: muted, marginBottom: 16 }}><Mark width={49} /> 飞书 · 来信助手</div>
            <div style={{ fontSize: 35, fontWeight: 710 }}>来信摘要已送达</div>
            <div style={{ color: muted, fontSize: 28, marginTop: 9 }}>行动事项、会议变更和截止时间</div>
          </div>
        </div>
        <div style={{ position: 'absolute', inset: 0, background: glass, opacity: open,
          transform: `translateY(${70 * (1 - open)}px)` }}>
          <ChatHeader />
          <div style={{ padding: '23px 24px' }}>
            <div style={{ marginBottom: 13, textAlign: 'center', color: muted, fontSize: 24 }}>08:30</div>
            <div style={{ padding: '22px 24px', background: white, border, borderRadius: 23 }}>
              <div style={{ fontSize: 29, fontWeight: 720, marginBottom: 18 }}>最近 24 小时来信摘要</div>
              <div style={{ fontSize: 24, fontWeight: 720, color: '#844c35', marginBottom: 7 }}>优先处理</div>
              <div style={{ fontSize: 31, fontWeight: 710 }}>课程项目阶段材料提交</div>
              <div style={{ fontSize: 25, margin: '8px 0 15px' }}>下一步：上传项目说明到课程平台</div>
              <div style={{ padding: '10px 14px', background: '#e8eddb', borderRadius: 12,
                color: '#3e522f', fontSize: 31, fontWeight: 710 }}>截止：明天 18:00</div>
            </div>
            <div style={{ marginTop: 13, padding: '16px 22px', background: white, border, borderRadius: 21 }}>
              <div style={{ fontSize: 23, fontWeight: 670, color: muted, marginBottom: 5 }}>需要关注</div>
              <div style={{ fontSize: 29, fontWeight: 680 }}>课题组会议时间更新</div>
            </div>
          </div>
        </div>
      </Phone>
    </Stage>
  );
}

function Result({ from, children }: { from: number; children: ReactNode }) {
  const frame = useCurrentFrame();
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 17, marginTop: 31,
      padding: '9px 23px 12px', color: '#364d32', background: '#e5eddd',
      border: '1px solid #cedcc6', borderRadius: 18, fontSize: 47, lineHeight: 1.32,
      fontWeight: 700, letterSpacing: -1, ...enter(frame, from) }}>
      <span style={{ width: 12, height: 12, borderRadius: '50%', background: '#526c4b' }} />
      {children}
    </div>
  );
}

function BrowserWindow({ title, children, style }: {
  title: string; children: ReactNode; style?: CSSProperties;
}) {
  return (
    <div style={{ position: 'absolute', left: 820, top: 192, width: 974, height: 688, overflow: 'hidden',
      borderRadius: 28, background: white, border: '1px solid #bfcdb9',
      boxShadow: '0 46px 77px -40px #27302499, 0 7px 21px #27302433', ...style }}>
      <div style={{ height: 67, display: 'flex', alignItems: 'center', gap: 19,
        padding: '0 24px', boxSizing: 'border-box', background: '#e8ede3', borderBottom: border,
        color: muted, fontSize: 29, fontWeight: 650 }}>
        <div style={{ display: 'flex', gap: 7 }}>
          {['#a2b39a', '#b3c0a8', '#c0ccba'].map(color => (
            <i key={color} style={{ width: 12, height: 12, background: color, borderRadius: 12 }} />
          ))}
        </div>
        {title}
      </div>
      <div style={{ position: 'absolute', inset: '67px 0 0', overflow: 'hidden' }}>{children}</div>
    </div>
  );
}

function Capture({ page, style }: { page: string; style?: CSSProperties }) {
  return <Img src={staticFile('textures/' + page + '.png')} style={{
    position: 'absolute', display: 'block', width: '100%', ...style,
  }} />;
}

export function WebScene(props: PhoneSceneProps) {
  const frame = useCurrentFrame();
  const digest = ramp(frame, 62, 94);
  const original = ramp(frame, 139, 164);
  return (
    <Stage {...props} copyWidth={655} titleSize={84} demo="网页演示工作台 · 样例邮件"
      detail={<Result from={99}>明天 18:00 前提交</Result>}>
      <BrowserWindow title={frame < 80 ? '来信 · 网页工作台' : '来信 · 来信摘要'}>
        <Capture page="mail" style={{ top: 0, left: 0, opacity: 1 - digest }} />
        <div style={{ position: 'absolute', inset: 0, opacity: digest,
          transform: `translateX(${48 * (1 - digest)}px)` }}>
          <Capture page="digest" style={{ width: 1250, left: -185, top: -158, opacity: 1 - .25 * original }} />
        </div>
        <div style={{ position: 'absolute', left: 164, right: 52, top: 172, padding: '24px 27px',
          background: white, color: ink, border: '1px solid #d9e1d4', borderRadius: 20,
          boxShadow: '0 23px 56px -34px #2730247a', opacity: digest * (1 - original),
          transform: `translateY(${24 * (1 - digest)}px)` }}>
          <div style={{ fontSize: 24, color: '#844c35', fontWeight: 690 }}>优先处理</div>
          <h2 style={{ fontSize: 40, margin: '8px 0 10px' }}>课程项目阶段材料提交</h2>
          <div style={{ color: muted, fontSize: 28 }}>上传项目说明到课程平台</div>
          <div style={{ fontSize: 29, display: 'flex', justifyContent: 'space-between', marginTop: 14,
            color: '#43623b', fontWeight: 630 }}>
            <span>截止 · 明天 18:00</span><span>邮件依据 ↗</span>
          </div>
        </div>
        <div style={{ position: 'absolute', left: 168, right: 71, top: 78, height: 449, overflow: 'hidden',
          borderRadius: 20, border, boxShadow: '0 22px 48px -20px #2730245c', background: white,
          opacity: original, transform: `translateY(${26 * (1 - original)}px)` }}>
          <Capture page="source-original" style={{ inset: 'auto 0 0', top: 0 }} />
        </div>
      </BrowserWindow>
    </Stage>
  );
}

function Bubble({ children, from, mine = false }: { children: ReactNode; from: number; mine?: boolean }) {
  const frame = useCurrentFrame();
  return (
    <div style={{ alignSelf: mine ? 'end' : 'start', maxWidth: '95%', padding: '12px 19px',
      fontSize: 30, fontWeight: mine ? 650 : 560, lineHeight: 1.25,
      borderRadius: mine ? '21px 21px 7px 21px' : '21px 21px 21px 7px',
      background: mine ? '#dcecf3' : white, color: mine ? '#214e61' : ink, border: mine ? '1px solid #c8dce5' : border,
      ...enter(frame, from, 14) }}>
      {children}
    </div>
  );
}

const first = '组会改到几点？';
const second = '地点呢？';
const firstSend = 42;
const secondSend = 117;

function Composer({ frame }: { frame: number }) {
  const secondTurn = frame >= 91;
  const text = secondTurn ? second : first;
  const cue = secondTurn ? 91 : 7;
  const send = secondTurn ? secondSend : firstSend;
  const count = Math.max(0, Math.floor((frame - cue) / 3) + 1);
  const caret = frame >= cue && frame < send &&
    (count <= text.length || Math.floor((frame - cue - text.length * 3) / 8) % 2 === 0);
  const composing = frame >= cue && frame < send;
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, display: 'flex', gap: 10,
      alignItems: 'center', padding: '12px 23px', background: glass, borderTop: border }}>
      <div style={{ flex: 1, minHeight: 56, display: 'flex', alignItems: 'center',
        border: '1px solid #d8e0d5', padding: '0 16px', background: white, borderRadius: 15,
        fontSize: 25, color: composing ? ink : muted }}>
        {composing ? text.slice(0, count) : '给来信助手发消息'}
        {caret && <i style={{ width: 2, marginLeft: 3, height: 28, background: blue }} />}
      </div>
      <div style={{ display: 'grid', placeItems: 'center', width: 56, height: 56,
        background: composing ? blue : '#59737a', color: white, borderRadius: 15, fontSize: 31 }}>
        ↑
      </div>
    </div>
  );
}

export function ConversationScene(props: PhoneSceneProps) {
  const frame = useCurrentFrame();
  return (
    <Stage {...props} detail={<Result from={150}>周五 16:00 · B202</Result>}>
      <Phone status="08:32">
        <ChatHeader />
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'start', gap: 13,
          padding: '18px 24px', boxSizing: 'border-box' }}>
          <Bubble from={firstSend} mine>{first}</Bubble>
          <Bubble from={64}>组会改到 <strong>周五 16:00</strong>。</Bubble>
          <Bubble from={secondSend} mine>{second}</Bubble>
          <Bubble from={140}>
            地点是 <strong style={{ color: blue, fontSize: 39 }}>B202</strong>。
            <div style={{ color: muted, fontSize: 23, lineHeight: 1.36, paddingTop: 8 }}>
              邮件依据：课题组会议时间更新<br />来源：课题组与实验室 · 课题组导师
            </div>
          </Bubble>
          {((frame >= 47 && frame < 64) || (frame >= 123 && frame < 140)) && (
            <div style={{ position: 'absolute', bottom: 110, left: 30, color: muted, fontSize: 23 }}>正在检索相关邮件…</div>
          )}
        </div>
        <Composer frame={frame} />
      </Phone>
    </Stage>
  );
}

export function ClosingScene(props: PhoneSceneProps) {
  const frame = useCurrentFrame();
  const inFrame = ramp(frame, 0, 31);
  return (
    <Stage {...props} dark copyWidth={630} titleSize={85} demo="网页工作台 · 手机飞书" detail={
      <div style={{ marginTop: 35, display: 'flex', alignItems: 'center', gap: 18, ...enter(frame, 23) }}>
        <Mark width={75} />
        <div style={{ fontSize: 49, fontWeight: 690, letterSpacing: 5 }}>来信</div>
      </div>
    }>
      <BrowserWindow title="网页工作台" style={{ left: 791, top: 291, width: 790, height: 490, ...enter(frame, 8) }}>
        <Capture page="digest" style={{ width: 891, top: -62, left: -33 }} />
      </BrowserWindow>
      <Phone status="08:32" background="#e6edde"
        style={{ left: 1352, top: 205, transformOrigin: 'top left',
          transform: `translateY(${48 * (1 - inFrame)}px) scale(.69) rotate(${6 - 3 * inFrame}deg)` }}>
        <ChatHeader />
        <div style={{ margin: '70px 26px 0', padding: '45px 29px', background: white,
          borderRadius: 29, border, ...enter(frame, 10) }}>
          <div style={{ fontSize: 30, color: muted }}>会议更新</div>
          <div style={{ fontSize: 67, fontWeight: 720, margin: '16px 0 8px', letterSpacing: -3 }}>周五 16:00</div>
          <div style={{ fontSize: 43, fontWeight: 660, color: blue }}>地点 B202</div>
        </div>
        <div style={{ margin: '18px 26px 0', color: muted, fontSize: 27,
          padding: '16px 29px', ...enter(frame, 22) }}>样例消息 · 飞书单聊</div>
      </Phone>
    </Stage>
  );
}
