import { paper } from './phone-scenes';
import { Promo, fps, height, music, sfx, shots, total, width } from './timeline';

export const WORKBENCH = {
  name: '来信 · 网页与飞书',
  fps,
  width,
  height,
  total,
  background: paper,
  original: Promo,
  sfx,
  bgm: [music],
  shots: shots.map((shot) => ({
    ...shot,
    schema: [
      { type: 'textarea', key: 'title', label: '标题', default: shot.props.title },
      { type: 'textarea', key: 'subtitle', label: '副标题', default: shot.props.subtitle },
      { type: 'text', key: 'label', label: '功能标签', default: shot.props.label },
      { type: 'color', key: 'accent', label: '强调色', default: shot.props.accent },
    ],
  })),
};
