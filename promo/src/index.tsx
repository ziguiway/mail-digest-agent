import { Composition, registerRoot } from 'remotion';
import { Promo as Overview, total as overviewLength } from './overview';
import { Promo, fps, height, total, width } from './timeline';

function Root() {
  return (
    <>
      <Composition id="LaixinPromo" component={Promo} width={width} height={height}
        durationInFrames={total} fps={fps} defaultProps={{ bgm: true }} />
      <Composition id="LaixinOverview" component={Overview} width={width} height={height}
        durationInFrames={overviewLength} fps={fps} defaultProps={{ bgm: true }} />
    </>
  );
}

registerRoot(Root);
