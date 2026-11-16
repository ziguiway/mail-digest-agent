import type { CSSProperties, ReactNode } from 'react';
import { AbsoluteFill, Easing, Img, interpolate, staticFile, useCurrentFrame } from 'remotion';
import { PageCam, type CamKey } from './PageCam';
import capture from './capture-layout.json';

export const paper = '#f7f6f3';
const white = '#fbfbf9';
const ink = '#38402f';
const charcoal = '#2f3437';
const font = '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, sans-serif';
const shadow = '0 36px 70px -38px #38402f66, 0 4px 14px #38402f17';
const ease = Easing.bezier(.33, 0, .15, 1);
const panelWidth = 1160;
const panelHeight = 710;
const layout = capture;

export type SceneProps = {
  title: string;
  subtitle: string;
  accent: string;
  label: string;
};
type BrandProps = SceneProps & { ending?: boolean };
type PageName = keyof typeof layout;

function ramp(frame: number, a: number, b: number, curve = ease) {
  return interpolate(frame, [a, b], [0, 1], {
    easing: curve,
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
}

function Picture({ name, style }: { name: string; style?: CSSProperties }) {
  return <Img src={staticFile('textures/' + name + '.png')} style={{ display: 'block', ...style }} />;
}

function Envelope({ size, dark = false }: { size: number; dark?: boolean }) {
  const edge = dark ? '#a6b193' : '#adb8a0';
  const fill = dark ? '#48523e' : '#f4f5ee';
  return (
    <div style={{ width: size, height: size * .67, position: 'relative', borderRadius: size * .079,
      overflow: 'hidden', background: fill, border: `2px solid ${edge}`, boxShadow: shadow }}>
      <div style={{ position: 'absolute', left: '50%', width: '71%', aspectRatio: 1,
        top: '-78%', background: dark ? '#59654e' : '#eef1e5',
        transform: 'translateX(-50%) rotate(45deg)', borderRight: `2px solid ${edge}`,
        borderBottom: `2px solid ${edge}`, borderRadius: size * .028 }} />
    </div>
  );
}

export function BrandScene({ title, subtitle, accent, label, ending = false }: BrandProps) {
  const frame = useCurrentFrame();
  const open = ramp(frame, 0, 48);
  const copyIn = ramp(frame, 8, 39);
  const cardIn = ramp(frame, 8, 55);
  const top = ending ? 240 : 300;
  return (
    <AbsoluteFill style={{ fontFamily: font, color: paper, background:
      `radial-gradient(ellipse at ${ending ? '50% 34%' : '75% 38%'}, ${accent} 0%, #38402f 55%, #282f24 100%)` }}>
      <div style={{ position: 'absolute', inset: 70, border: '1px solid #ecf0e627', borderRadius: 26 }} />
      {ending ? (
        <div style={{ position: 'absolute', top: 287, width: '100%', display: 'grid', justifyItems: 'center',
          opacity: copyIn, transform: `translateY(${18 * (1 - copyIn)}px)` }}>
          <Envelope size={132} />
          <h1 style={{ fontSize: 128, letterSpacing: 13, margin: '32px 0 12px', lineHeight: 1.3 }}>{title}</h1>
          <div style={{ fontSize: 34, color: '#e6ebdb' }}>{subtitle}</div>
        </div>
      ) : (
        <>
          <div style={{ position: 'absolute', left: 120, top, opacity: copyIn,
            transform: `translateY(${22 * (1 - copyIn)}px)`, zIndex: 2 }}>
            <div style={{ fontSize: 32, letterSpacing: 6, color: '#e0e9d2' }}>{label}</div>
            <h1 style={{ fontSize: 88, lineHeight: 1.24, letterSpacing: -4, fontWeight: 650,
              whiteSpace: 'pre-line', margin: '36px 0 35px' }}>{title}</h1>
            <div style={{ fontSize: 33, color: '#e0e9d2' }}>{subtitle}</div>
          </div>
          <div style={{ position: 'absolute', left: 1265, top: 350, width: 455, height: 340,
            transform: `translateY(${62 * (1 - open)}px) rotate(${-16 + 7 * open}deg)` }}>
            <div style={{ position: 'absolute', left: 54, top: -88 * cardIn, width: 340, height: 223,
              background: '#e8eddc', border: '1px solid #c2cdb6', borderRadius: 19, padding: '35px 36px',
              transform: `rotate(${8 + 5 * cardIn}deg)`, boxShadow: shadow }}>
              <div style={{ width: 112, height: 11, borderRadius: 20, background: '#9ba990', marginBottom: 28 }} />
              {[230, 211, 170].map((width) => <div key={width} style={{width, height: 5, borderRadius: 9,
                background: '#bcc7b2', marginBottom: 15 }} />)}
            </div>
            <div style={{ position: 'absolute', bottom: 0 }}><Envelope size={455} /></div>
          </div>
        </>
      )}
    </AbsoluteFill>
  );
}

function Feature({ title, subtitle, accent, label, detail, children }: SceneProps & {
  detail?: string;
  children: ReactNode;
}) {
  const frame = useCurrentFrame();
  const enter = ramp(frame, 0, 21);
  return (
    <AbsoluteFill style={{ fontFamily: font, color: ink,
      background: 'radial-gradient(ellipse at 76% 38%, #e7eddf 0%, #f7f6f3 68%)' }}>
      <div style={{ position: 'absolute', top: 87, left: 108, right: 104,
        paddingBottom: 22, borderBottom: '2px solid #d6dece', color: accent,
        display: 'flex', justifyContent: 'space-between', fontSize: 25, fontWeight: 650, letterSpacing: 2.5 }}>
        <span>来信 · 样例演示</span><span>{label}</span>
      </div>
      <div style={{ position: 'absolute', top: 329, left: 108, width: 560, opacity: enter,
        transform: `translateY(${20 * (1 - enter)}px)` }}>
        <h1 style={{ whiteSpace: 'pre-line', fontSize: 73, lineHeight: 1.22, letterSpacing: -3.5,
          margin: '0 0 36px', fontWeight: 650 }}>{title}</h1>
        <div style={{ fontSize: 35, lineHeight: 1.58, color: '#525d4a', whiteSpace: 'pre-line' }}>{subtitle}</div>
        {detail && (
          <div style={{ marginTop: 37, display: 'inline-flex', alignItems: 'center', gap: 14,
            background: '#e8eddc', border: '1px solid #d5dec8', padding: '12px 20px',
            fontSize: 27, color: accent, borderRadius: 12, fontWeight: 640 }}>
            <div style={{ width: 9, height: 9, borderRadius: '50%', background: accent }} />
            {detail}
          </div>
        )}
      </div>
      <div style={{ position: 'absolute', left: 656, top: 193, width: panelWidth, height: 785 }}>{children}</div>
      <div style={{ position: 'absolute', bottom: 55, left: 108, right: 104,
        height: 2, background: '#d6dece' }} />
    </AbsoluteFill>
  );
}

function CapturedWindow({ name, keys, children, style }: {
  name: PageName;
  keys: CamKey[];
  children?: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div style={{ position: 'absolute', inset: '0 auto auto 0', width: panelWidth, height: panelHeight,
      border: '1px solid #d6dece', borderRadius: 25, overflow: 'hidden', background: white, boxShadow: shadow, ...style }}>
      <PageCam src={'textures/' + name + '.png'} pageH={layout[name].pageH}
        screenW={panelWidth} screenH={panelHeight} keys={keys}>{children}</PageCam>
    </div>
  );
}

export function MailScene(props: SceneProps) {
  const frame = useCurrentFrame();
  const enter = ramp(frame, 0, 30);
  const row = layout.mail.regions.first;
  const cx = row.x + row.w / 2;
  const messages = [
    ['课程与项目', '课程项目阶段材料提交', 'QQ 邮箱'],
    ['课题组与实验室', '课题组会议时间更新', '网易邮箱'],
    ['课题组与实验室', '实验室设备借用确认', '网易邮箱'],
  ];
  return (
    <Feature {...props} detail="QQ 邮箱 · 网易邮箱">
      <CapturedWindow name="mail" keys={[
        { frame: 0, cx: cx - 20, cy: 392, zoom: .78, rotY: -8, rotX: 4 },
        { frame: 75, cx, cy: 380, zoom: .93, rotY: 0, rotX: 0 },
      ]} style={{ opacity: .66 * enter, transform: `translateX(${74 * (1 - enter)}px)` }} />
      <div style={{ position: 'absolute', top: 210, left: 112, right: 86, display: 'grid', gap: 16 }}>
        {messages.map(([label, subject, provider], index) => {
          const cue = 25 + index * 9 - index * (index - 1);
          const deal = ramp(frame, cue, cue + 16, Easing.bezier(.3, 0, .2, 1));
          const settle = ramp(frame, cue + 16, cue + 22, Easing.bezier(.3, 0, .25, 1.15));
          return (
            <div key={subject} style={{ opacity: deal, display: 'flex', alignItems: 'center',
              justifyContent: 'space-between', padding: '19px 30px', border: '1px solid #dce4d2',
              background: white, borderRadius: 16, boxShadow: '0 9px 28px #38402f18',
              transform: `translate(${120 * (1 - deal)}px, ${6 * deal * (1 - settle)}px) scale(${.96 + .04 * deal})` }}>
              <div>
                <div style={{ color: '#5c6952', fontSize: 21, fontWeight: 640 }}>{label}</div>
                <div style={{ color: charcoal, fontSize: 36, fontWeight: 650 }}>{subject}</div>
              </div>
              <div style={{ color: props.accent, fontSize: 23, padding: '8px 16px', borderRadius: 12,
                fontWeight: 650, background: '#e9eee2' }}>{provider}</div>
            </div>
          );
        })}
      </div>
    </Feature>
  );
}

export function DigestScene(props: SceneProps) {
  const frame = useCurrentFrame();
  const item = layout.digest.regions.action;
  const cx = item.x + item.w / 2;
  const rise = ramp(frame, 56, 66, Easing.bezier(.2, 1.25, .3, 1));
  const reseat = ramp(frame, 102, 121, Easing.bezier(.4, 0, .3, 1.05));
  const lift = rise * (1 - reseat);
  const original = ramp(frame, 113, 142);
  const focus = ramp(frame, 24, 55) * (1 - original);
  return (
    <Feature {...props} detail="无确切期限 · 截止时间未明确">
      <CapturedWindow name="digest" keys={[
        { frame: 0, cx: cx - 45, cy: item.y + 87, zoom: .8 },
        { frame: 24, cx: cx - 45, cy: item.y + 87, zoom: .8 },
        { frame: 58, cx, cy: item.y + 65, zoom: .93 },
      ]} style={{ transform: `scale(${1 - .045 * original})`, opacity: 1 - .25 * original }}>
        <div style={{ position: 'absolute', inset: 0, background: '#38402f',
          opacity: .12 * lift, height: layout.digest.pageH }} />
        <Picture name="digest-action" style={{ position: 'absolute', left: item.x, top: item.y - 10 * lift,
          width: item.w, height: item.h, borderRadius: 12, opacity: lift,
          boxShadow: `0 ${20 * lift}px ${45 * lift}px #38402f45` }} />
      </CapturedWindow>
      <div style={{ position: 'absolute', top: 244, left: 150, right: 103, borderRadius: 18,
        border: '1px solid #dfddcd', background: white, padding: '35px 42px 38px',
        boxShadow: shadow, opacity: focus, transform: `translateY(${14 * (1 - focus) - 12 * lift}px)` }}>
        <div style={{ color: '#8b523d', fontSize: 25, fontWeight: 650 }}>优先处理</div>
        <div style={{ fontSize: 42, fontWeight: 670, margin: '12px 0 19px' }}>课程项目阶段材料提交</div>
        <div style={{ color: charcoal, fontSize: 31 }}>上传项目说明到课程平台</div>
        <div style={{ marginTop: 19, display: 'flex', justifyContent: 'space-between',
          color: props.accent, fontSize: 25, fontWeight: 650 }}>
          <span>截止 · 明天 18:00</span><span>查看邮件依据 ↗</span>
        </div>
      </div>
      {original > 0 && (
        <div style={{ position: 'absolute', left: 132, top: 127, width: 896, border: '1px solid #c9d3c1',
          borderRadius: 20, overflow: 'hidden', boxShadow: shadow, background: white,
          opacity: original, transform: `translateY(${36 * (1 - original)}px)` }}>
          <Picture name="source-original" style={{ width: '100%' }} />
        </div>
      )}
    </Feature>
  );
}

export function QueryScene(props: SceneProps) {
  const frame = useCurrentFrame();
  const query = '后来有更新吗？';
  const typeStart = 12;
  const complete = typeStart + query.length * 3;
  const count = Math.max(0, Math.floor((frame - typeStart) / 3) + 1);
  const visible = frame >= typeStart - 2 && (frame < complete || Math.floor((frame - complete) / 8) % 2 === 0);
  const reveal = ramp(frame, complete + 11, complete + 38);
  return (
    <Feature {...props} detail="周五 16:00 · B202">
      <div style={{ position: 'absolute', inset: '0 0 auto', height: panelHeight, borderRadius: 25,
        padding: '56px 62px', background: white, border: '1px solid #d6dece', boxShadow: shadow }}>
        <div style={{ fontSize: 29, fontWeight: 650, color: '#5a6751', marginBottom: 31 }}>找一下导师的会议通知。</div>
        <div style={{ background: '#eaefe2', border: '1px solid #dce6d1', borderRadius: 17,
          minHeight: 92, padding: '20px 33px', fontSize: 46, lineHeight: 1.12, fontWeight: 600, color: charcoal }}>
          {query.slice(0, count)}
          <span style={{ display: 'inline-block', height: 43, width: 3, marginLeft: 8,
            verticalAlign: '-6px', background: props.accent, opacity: visible ? 1 - reveal : 0 }} />
        </div>
        <div style={{ marginTop: 30, opacity: reveal, transform: `translateY(${22 * (1 - reveal)}px)` }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: 16, margin: '0 0 18px', fontSize: 27, color: props.accent }}>
            <strong style={{ color: ink, fontSize: 43 }}>周五 16:00 · B202</strong>
            <span>邮件依据 · 课题组会议时间更新</span>
          </div>
          <div style={{ overflow: 'hidden', border: '1px solid #e3e7df', borderRadius: 16 }}>
            <Picture name="query-answer" style={{ width: '100%' }} />
          </div>
        </div>
      </div>
    </Feature>
  );
}

export function ScheduleScene(props: SceneProps) {
  const frame = useCurrentFrame();
  const item = layout.schedule.regions.plan;
  const cx = item.x + item.w / 2;
  const arrived = ramp(frame, 38, 70);
  return (
    <Feature {...props} detail="每日 08:30">
      <CapturedWindow name="schedule" keys={[
        { frame: 0, cx, cy: item.y + 210, zoom: .94 },
        { frame: 70, cx: cx + 45, cy: item.y + 198, zoom: .91 },
      ]} />
      <aside style={{ position: 'absolute', left: 402, top: 295, width: 690, padding: '38px 43px 40px',
        transform: `translateY(${55 * (1 - arrived)}px)`, opacity: arrived,
        color: charcoal, background: white, border: '2px solid #d6dece',
        borderRadius: 23, boxShadow: shadow }}>
        <div style={{ fontSize: 35, color: props.accent, fontWeight: 650, marginBottom: 20 }}>飞书 · 消息示意</div>
        <div style={{ fontSize: 37, fontWeight: 680, lineHeight: 1.35 }}>需要关注 · 组会时间更新</div>
        <div style={{ fontSize: 29, lineHeight: 1.55, marginTop: 17 }}>周五 16:00，地点 B202。<br />下一步：确认参会，准备研究进展。</div>
      </aside>
    </Feature>
  );
}
