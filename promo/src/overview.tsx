import { AbsoluteFill, Audio, Sequence, staticFile } from 'remotion';
import { BrandScene, DigestScene, MailScene, QueryScene, ScheduleScene, paper } from './scenes';

export const fps = 30;
export const width = 1920;
export const height = 1080;

const featureColor = '#4b6045';
const brandColor = '#647353';
const definitions = [
  { id: 'opening', label: '品牌开场', duration: 120, component: BrandScene, props: {
    title: '把多个邮箱，\n读成一份清楚的安排。', subtitle: '给忙于课程、科研和项目的你。', label: '来信', accent: brandColor,
  } },
  { id: 'inbox', label: '多邮箱聚合', duration: 150, component: MailScene, props: {
    title: '散在各处，\n收到一处。', subtitle: '课程通知、项目材料、会议安排，\n在同一个工作台查看。', label: '多邮箱聚合', accent: featureColor,
  } },
  { id: 'digest', label: '智能摘要与依据', duration: 210, component: DigestScene, props: {
    title: '重要的事，\n先被看见。', subtitle: '按优先级整理下一步和期限。\n点开邮件依据，核对原文。', label: '智能摘要', accent: featureColor,
  } },
  { id: 'query', label: '多轮追问', duration: 240, component: QueryScene, props: {
    title: '一句追问，\n找到最新安排。', subtitle: '从相关邮件和往来找答案。\n会议时间、地点，都有出处。', label: '自然语言查询', accent: featureColor,
  } },
  { id: 'schedule', label: '定时汇总与飞书', duration: 180, component: ScheduleScene, props: {
    title: '每天按时，\n把重点送到。', subtitle: '启用汇总计划并绑定飞书，\n按时收摘要，在单聊里继续问。', label: '定时汇总 · 飞书', accent: featureColor,
  } },
  { id: 'closing', label: '品牌落版', duration: 120, component: BrandScene, props: {
    title: '来信', subtitle: '多邮箱智能助手 · 基于 Agently', label: '', accent: brandColor, ending: true,
  } },
];

let position = 0;
export const shots = definitions.map((shot) => {
  const from = position;
  position += shot.duration;
  return { ...shot, from };
});
export const total = position;

export const music = { from: 0, duration: total, volume: .76, src: 'audio/laixin-score.mp3' };
export const sfx = [
  { from: shots[0].from + 29, duration: 12, src: 'audio/sfx/laixin-slide-470189.wav', volume: .90 },
  { from: shots[2].from + 50, duration: 14, src: 'audio/sfx/laixin-slide-648387.wav', volume: .83 },
  { from: shots[4].from + 54, duration: 12, src: 'audio/sfx/laixin-slide-470189.wav', volume: .84 },
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
