import { AbsoluteFill, Audio, Sequence, staticFile } from 'remotion';
import {
  ArrivalScene, ClosingScene, ConversationScene, FrictionScene, WebScene, paper,
} from './phone-scenes';

export const fps = 30;
export const width = 1920;
export const height = 1080;

const featureColor = '#465d43';
const brandColor = '#53674b';
const definitions = [
  { id: 'switch-mail', label: '逐封查邮件', duration: 150, component: FrictionScene, props: {
    title: '找一条会议变更，\n要翻几封邮件？',
    subtitle: '从 QQ 邮箱切到网易邮箱，\n一封封点开，才看到更新。',
    label: '逐封找邮件', accent: brandColor,
  } },
  { id: 'read', label: '网页聚合与核对', duration: 240, component: WebScene, props: {
    title: '打开网页，\n来信归到一起。',
    subtitle: 'QQ、网易都在一个工作台。\n看待办和期限，点开核对原文。',
    label: '入口一 · 网页工作台', accent: featureColor,
  } },
  { id: 'receive', label: '飞书收摘要', duration: 180, component: ArrivalScene, props: {
    title: '打开飞书，\n重点已经到了。',
    subtitle: '绑定飞书并启用定时汇总，\n每天在手机单聊收摘要。',
    label: '入口二 · 手机飞书', accent: featureColor,
  } },
  { id: 'ask', label: '在手机上接着问', duration: 240, component: ConversationScene, props: {
    title: '想确认细节，\n在飞书直接问。',
    subtitle: '问完时间，再问地点。\n来信从相关邮件和往来里找答案。',
    label: '多轮追问', accent: featureColor,
  } },
  { id: 'sign-off', label: '品牌落版', duration: 150, component: ClosingScene, props: {
    title: '网页看全局，\n手机随手查。',
    subtitle: '坐下来核对原文，出门用飞书查。',
    label: '两个入口，一份来信重点', accent: brandColor,
  } },
];

let position = 0;
export const shots = definitions.map((shot) => {
  const from = position;
  position += shot.duration;
  return { ...shot, from };
});
export const total = position;
export const music = { from: 0, duration: total, volume: .76, src: 'audio/laixin-dual-score.mp3' };
export const sfx = [
  { from: shots[0].from + 50, duration: 12, src: 'audio/sfx/laixin-slide-470189.wav', volume: .86 },
  { from: shots[2].from + 34, duration: 14, src: 'audio/sfx/laixin-slide-648387.wav', volume: .88 },
];

export function Promo({ bgm = true }: { bgm?: boolean }) {
  return (
    <AbsoluteFill style={{ background: paper }}>
      {shots.map(({ id, component: Shot, from, duration, props }) => (
        <Sequence key={id} name={id} from={from} durationInFrames={duration}>
          <Shot {...props} />
        </Sequence>
      ))}
      {[...sfx, ...(bgm ? [music] : [])].map(({ from, duration, volume, src }) => (
        <Sequence key={src + from} from={from} durationInFrames={duration} layout="none">
          <Audio src={staticFile(src)} volume={volume} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
}
