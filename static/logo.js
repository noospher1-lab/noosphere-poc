// Noosphere logo — 3D Fibonacci-sphere renderer, ported verbatim from the
// marketing site (index.html) so the PoC logo is pixel-identical. Header
// variant only (the hero instance is omitted). Drives #logoCanvas.

(function () {
  const COL_BLUE  = [95, 168, 255];
  const COL_WHITE = [255, 250, 235];
  const COL_GOLD  = [216, 175, 110];
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function lerp(a, b, t) { return a + (b - a) * t; }
  function lerpColor(c1, c2, t) {
    return [
      Math.round(lerp(c1[0], c2[0], t)),
      Math.round(lerp(c1[1], c2[1], t)),
      Math.round(lerp(c1[2], c2[2], t))
    ];
  }

  function fibonacciSphere(n) {
    const pts = [];
    const phi = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < n; i++) {
      const y = 1 - (i / (n - 1)) * 2;
      const radius = Math.sqrt(Math.max(0, 1 - y * y));
      const theta = phi * i;
      pts.push({ x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius });
    }
    return pts;
  }

  // Build the point set once; shared by all instances (geometry is identical).
  function buildModel(outerN, innerN) {
    const outer = fibonacciSphere(outerN).map(p => {
      // hemispheric duality: sign of x picks blue vs gold, blended toward
      // white along the x≈0 seam so the two flows meet rather than clash.
      const seam = Math.max(0, 1 - Math.abs(p.x) / 0.28);
      const base = p.x < 0 ? COL_BLUE : COL_GOLD;
      const col = lerpColor(base, COL_WHITE, seam * 0.85);
      return { x: p.x, y: p.y, z: p.z, col, layer: 'outer' };
    });

    const inner = [];
    for (let i = 0; i < innerN; i++) {
      const r = Math.cbrt(Math.random()) * 0.6;
      const theta = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      const x = r * Math.sin(ph) * Math.cos(theta);
      const y = r * Math.sin(ph) * Math.sin(theta) * 0.9;
      const z = r * Math.cos(ph) * 0.85;
      const seam = Math.max(0, 1 - Math.abs(x) / 0.14);
      const base = x < 0 ? COL_BLUE : COL_GOLD;
      const col = lerpColor(base, COL_WHITE, seam * 0.9);
      inner.push({ x, y, z, col, layer: 'inner' });
    }

    const all = outer.concat(inner);

    // sparse wireframe: connect neighbouring outer points around the ring
    const outerIdx = [];
    all.forEach((p, i) => { if (p.layer === 'outer') outerIdx.push(i); });
    outerIdx.sort((a, b) =>
      Math.atan2(all[a].z, all[a].x) - Math.atan2(all[b].z, all[b].x));
    const links = [];
    for (let k = 0; k < outerIdx.length; k++) {
      links.push([outerIdx[k], outerIdx[(k + 1) % outerIdx.length]]);
    }
    return { all, links };
  }

  // One reusable glow sprite per (color,size) is overkill; instead we make a
  // single white glow sprite and tint it per-point cheaply via globalAlpha +
  // a colored fill under 'source-atop' at build time, per unique color bucket.
  function makeGlowSprite(rgb, size) {
    const s = document.createElement('canvas');
    s.width = s.height = size;
    const c = s.getContext('2d');
    const g = c.createRadialGradient(size/2, size/2, 0, size/2, size/2, size/2);
    g.addColorStop(0,   `rgba(${rgb[0]},${rgb[1]},${rgb[2]},1)`);
    g.addColorStop(0.4, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.35)`);
    g.addColorStop(1,   `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0)`);
    c.fillStyle = g;
    c.fillRect(0, 0, size, size);
    return s;
  }

  // quantize colors into a small palette of sprites (huge perf win vs
  // per-point gradients, visually indistinguishable at these sizes)
  function spriteCache(size) {
    const cache = new Map();
    return function (rgb) {
      const key = (rgb[0] >> 4) + '-' + (rgb[1] >> 4) + '-' + (rgb[2] >> 4);
      let sp = cache.get(key);
      if (!sp) { sp = makeGlowSprite(rgb, size); cache.set(key, sp); }
      return sp;
    };
  }

  function createInstance(canvas, opts) {
    const ctx = canvas.getContext('2d');
    const model = buildModel(opts.outerN, opts.innerN);
    const getSprite = spriteCache(64);
    const rotX = -0.18;
    let running = false, visible = true, tabVisible = true;
    let raf = null;

    function rotate(p, rotY) {
      const cy = Math.cos(rotY), sy = Math.sin(rotY);
      const x = p.x * cy - p.z * sy;
      const z = p.x * sy + p.z * cy;
      const cx2 = Math.cos(rotX), sx2 = Math.sin(rotX);
      return { x, y: p.y * cx2 - z * sx2, z: p.y * sx2 + z * cx2 };
    }

    function frame(now) {
      if (!running) return;
      const t = reduceMotion ? 0 : now / 1000;
      const rotOuter = t * 0.32;
      const rotInner = t * -0.46;

      const W = canvas.width, H = canvas.height;
      const cx = W / 2, cy = H * 0.5;
      const scale = Math.min(W, H) * 0.34;
      const camDist = 2.6;
      const res = canvas._res || 1;

      ctx.clearRect(0, 0, W, H);
      ctx.globalCompositeOperation = 'source-over';

      const rot = model.all.map(p => rotate(p, p.layer === 'outer' ? rotOuter : rotInner));
      const proj = rot.map(rp => {
        const persp = camDist / (rp.z + camDist);
        return { x: cx + rp.x * scale * persp, y: cy + rp.y * scale * persp, s: persp };
      });
      const order = model.all.map((_, i) => i).sort((i, j) => rot[i].z - rot[j].z);

      // links
      ctx.globalCompositeOperation = 'lighter';
      if (!opts.hideLinks) {
      ctx.lineWidth = (opts.lineWidth || 0.6) * (opts.scaleLines ? res : 1);
      for (let k = 0; k < model.links.length; k++) {
        const [ai, bi] = model.links[k];
        const avgZ = (rot[ai].z + rot[bi].z) / 2;
        const a = Math.max(0, (avgZ + 1) / 2) * (opts.lineAlpha || 0.16);
        if (a < 0.01) continue;
        const col = model.all[ai].col;
        ctx.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},${a})`;
        ctx.beginPath();
        ctx.moveTo(proj[ai].x, proj[ai].y);
        ctx.lineTo(proj[bi].x, proj[bi].y);
        ctx.stroke();
      }
      }

      // points via cached sprites
      for (let oi = 0; oi < order.length; oi++) {
        const i = order[oi];
        const p = model.all[i], pr = proj[i];
        const depth = Math.max(0.12, (rot[i].z + 1) / 2);
        const baseR = p.layer === 'inner' ? 1.7 : 2.0;
        const rad = baseR * pr.s * (0.7 + depth * 0.5) * 2.4 * opts.pointScale;
        const sp = getSprite(p.col);
        ctx.globalAlpha = depth * (p.layer === 'inner' ? 0.9 : 0.95) * (opts.pointBright || 1);
        ctx.drawImage(sp, pr.x - rad, pr.y - rad, rad * 2, rad * 2);
      }
      ctx.globalAlpha = 1;

      // central core
      const coreP = rotate({ x: 0, y: 0, z: 0 }, rotInner);
      const persp = camDist / (coreP.z + camDist);
      const ccx = cx + coreP.x * scale * persp, ccy = cy + coreP.y * scale * persp;
      const coreR = scale * 0.09;
      const cg = ctx.createRadialGradient(ccx, ccy, 0, ccx, ccy, coreR * 3.5);
      cg.addColorStop(0, 'rgba(255,250,235,0.9)');
      cg.addColorStop(0.4, 'rgba(255,210,150,0.4)');
      cg.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = cg;
      ctx.beginPath();
      ctx.arc(ccx, ccy, coreR * 3.5, 0, Math.PI * 2);
      ctx.fill();

      if (reduceMotion) { running = false; return; } // draw one static frame
      raf = requestAnimationFrame(frame);
    }

    function start() {
      if (running || !visible || !tabVisible) return;
      running = true; raf = requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      if (raf) cancelAnimationFrame(raf);
    }

    // pause when off-screen — only for large instances (hero). The header
    // logo lives in a fixed bar and must keep spinning regardless of the
    // observer's reading, so it opts out.
    if (opts.pauseOffscreen && 'IntersectionObserver' in window) {
      new IntersectionObserver((entries) => {
        visible = entries[0].isIntersecting;
        visible ? start() : stop();
      }, { threshold: 0.01 }).observe(canvas);
    }
    document.addEventListener('visibilitychange', () => {
      tabVisible = !document.hidden;
      tabVisible ? start() : stop();
    });

    return { start, stop, frameOnce: () => { running = true; frame(performance.now()); if (reduceMotion) running = false; } };
  }


  // ---- header logo: small, sparse, cheap (matches the site header) ----
  const logoCanvas = document.getElementById('logoCanvas');
  if (logoCanvas) {
    (function sizeLogo() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const eff = Math.max(dpr, 3);
      logoCanvas.width = 44 * eff;
      logoCanvas.height = 44 * eff;
      logoCanvas._res = eff;
    })();
    const logo = createInstance(logoCanvas, { outerN: 190, innerN: 250, pointScale: 0.7, hideLinks: true, pointBright: 1.7, pauseOffscreen: false });
    logo.start();
  }
})();
