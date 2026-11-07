/**
 * 记忆星图 · 3D 可交互（设置页「记忆星图」）
 *
 * 纯 Canvas 实现的三维星图，不依赖 three.js 等外部库：
 *   · 大区 = 星云球域，小区 = 星团，经验 = 三维空间里的星点
 *   · 相机为球面轨道相机：拖拽旋转、滚轮缩放、双击复位、可切换自动旋转
 *   · 透视投影 + 深度排序 + 近大远小/近亮远暗，冲突经验之间画空间连线
 *
 * 数据来自 Host 只读路由 /dsh-memory/starmap.json（与 CLI 同一份数据）。
 */
window.__ModuleLoader__.load({
  id: 'dsh-memory',
  factory(require) {
    const React = require('react')
    const { useCallback, useEffect, useMemo, useRef, useState } = React
    const h = React.createElement

    const MATURITY_COLOR = {
      draft: '#7aa7ff',
      active: '#3fd6a4',
      trusted: '#ffd166',
      flipped: '#ff5f74',
    }
    const MATURITY_LABEL = {
      draft: '待验证',
      active: '已验证',
      trusted: '高可信',
      flipped: '已翻车',
    }
    const TYPE_LABEL = { success: '成功经验', fail: '踩坑教训', neutral: '中性记录' }
    const ZONE_TINT = ['#5b8bff', '#8f6bff', '#2fc7d8', '#ff8a5b', '#4fd18b', '#d96bd8', '#ffd166']
    /** 待批提案（流星）按类型着色。 */
    const KIND_COLOR = {
      ingest: '#7affc8',
      freeze: '#7ad0ff',
      dissolve_subzone: '#ff9e6b',
      mirror_search: '#b48bff',
      permanent_migrate: '#ffd166',
    }
    const KIND_LABEL = {
      ingest: '入库提案',
      freeze: '冷封存提案',
      dissolve_subzone: '解散提案',
      mirror_search: '镜像检索提案',
      permanent_migrate: '永久回迁提案',
    }
    /** 角度差取最短路径，避免镜头绕远路。 */
    function shortestAngle(from, to) {
      let delta = (to - from) % (Math.PI * 2)
      if (delta > Math.PI) delta -= Math.PI * 2
      if (delta < -Math.PI) delta += Math.PI * 2
      return delta
    }

    /** 相机缺省姿态。 */
    const CAMERA_DEFAULT = { yaw: 0.7, pitch: -0.3, dist: 760, panX: 0, panY: 0, fov: 1000 }

    function styleTag() {
      return h('style', {
        'data-plugin-css': 'dsh-memory-starmap-3d',
        dangerouslySetInnerHTML: {
          __html: `
.dshmem-root{display:flex;flex-direction:column;gap:14px;height:100%;min-height:560px;color:var(--dsw-alias-label-primary)}
.dshmem-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}
.dshmem-title{font-size:16px;font-weight:600;margin:0 0 4px}
.dshmem-sub{font-size:12px;color:var(--dsw-alias-label-secondary);line-height:1.65;margin:0}
.dshmem-actions{display:flex;gap:8px;flex:none}
.dshmem-btn{appearance:none;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary);border-radius:8px;padding:6px 12px;font-size:12px;cursor:pointer;line-height:1.4;white-space:nowrap}
.dshmem-btn:hover{border-color:var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-1)}
.dshmem-btn[disabled]{opacity:.5;cursor:default}
.dshmem-btn.on{border-color:var(--dsw-alias-brand-primary);color:var(--dsw-alias-brand-primary)}
.dshmem-stage{position:relative;flex:1;min-height:480px;border-radius:14px;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);background:#05070f}
.dshmem-canvas{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab;touch-action:none}
.dshmem-canvas.is-dragging{cursor:grabbing}
.dshmem-tip{position:absolute;pointer-events:none;z-index:4;max-width:300px;padding:10px 12px;border-radius:10px;background:rgba(10,16,34,.94);border:1px solid rgba(140,170,255,.28);box-shadow:0 10px 30px rgba(0,0,0,.45);color:#e8eeff;font-size:12px;line-height:1.6}
.dshmem-tip b{color:#fff;font-weight:600}
.dshmem-tip .k{color:#8fa4d8}
.dshmem-card{position:absolute;top:12px;right:12px;z-index:3;width:300px;max-height:calc(100% - 24px);overflow:auto;border-radius:12px;background:rgba(10,16,34,.94);border:1px solid rgba(140,170,255,.24);box-shadow:0 14px 40px rgba(0,0,0,.5);color:#e8eeff;padding:14px}
.dshmem-card h4{margin:0 0 8px;font-size:13px;font-weight:600;color:#fff}
.dshmem-card .row{font-size:12px;line-height:1.65;color:#c9d4f2;margin:4px 0}
.dshmem-card .row .k{color:#8fa4d8}
.dshmem-card .tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.dshmem-card .tag{font-size:11px;padding:2px 8px;border-radius:999px;background:rgba(122,167,255,.16);color:#cfe0ff}
.dshmem-card .close{position:absolute;top:8px;right:10px;appearance:none;border:0;background:transparent;color:#8fa4d8;cursor:pointer;font-size:16px;line-height:1}
.dshmem-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;color:#9fb0d8;font-size:13px;text-align:center;pointer-events:none}
.dshmem-empty .big{font-size:15px;color:#dfe7ff}
.dshmem-legend{position:absolute;left:12px;bottom:12px;z-index:3;display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:11px;color:#a9b6d6;background:rgba(8,12,26,.72);border:1px solid rgba(120,150,220,.2);border-radius:10px;padding:6px 10px;backdrop-filter:blur(4px)}
.dshmem-legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:middle}
.dshmem-hud{position:absolute;left:12px;top:12px;z-index:3;display:flex;gap:8px;align-items:center;font-size:11px;color:#a9b6d6;background:rgba(8,12,26,.72);border:1px solid rgba(120,150,220,.2);border-radius:10px;padding:6px 10px;backdrop-filter:blur(4px)}
.dshmem-hint{position:absolute;right:12px;bottom:12px;z-index:3;font-size:11px;color:#7f90bb;background:rgba(8,12,26,.6);border-radius:8px;padding:4px 8px}
.dshmem-err{position:absolute;inset:12px;z-index:5;border-radius:10px;background:rgba(60,10,20,.9);border:1px solid rgba(255,95,116,.5);color:#ffd7dd;font-size:12px;line-height:1.6;padding:12px;overflow:auto}
`,
        },
      })
    }

    function hash(text) {
      let x = 2166136261
      for (let i = 0; i < text.length; i += 1) {
        x ^= text.charCodeAt(i)
        x = Math.imul(x, 16777619)
      }
      return x >>> 0
    }
    function pseudo(text, salt) {
      return (hash(text + '|' + salt) % 10000) / 10000
    }
    function groupBy(list, keyOf) {
      const map = new Map()
      for (const item of list) {
        const key = keyOf(item)
        if (!map.has(key)) map.set(key, [])
        map.get(key).push(item)
      }
      return map
    }
    function hexA(hex, alpha) {
      const m = /^#?([0-9a-f]{6})$/i.exec(String(hex))
      if (!m) return `rgba(160,180,240,${alpha})`
      const n = parseInt(m[1], 16)
      return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
    }
    /** 球面均匀分布的第 i/n 个点（Fibonacci 球）。 */
    function spherePoint(i, n, radius) {
      const t = n <= 1 ? 0.5 : (i + 0.5) / n
      const phi = Math.acos(1 - 2 * t)
      const theta = Math.PI * (1 + Math.sqrt(5)) * i
      const r = radius * (0.55 + 0.45 * Math.cbrt(Math.max(t, 0.05)))
      return {
        x: Math.cos(theta) * Math.sin(phi) * r,
        y: Math.cos(phi) * r * 0.8,
        z: Math.sin(theta) * Math.sin(phi) * r,
      }
    }

    /** 把记忆库铺成三维场景（坐标单位约为 1px @ 相机距离 980）。 */
    function buildScene3D(data) {
      const stars = []
      const clusters = []
      const zones = []
      const links = []
      const dust = []
      const byId = new Map()

      const zonesList = data.zones && data.zones.length > 0
        ? data.zones
        : [{ id: 'z_default', name: '未分区', tags: [] }]
      const units = data.units || []
      const cold = data.cold || []
      const subzones = (data.subzones || []).filter((s) => s.status === 'active')
      const unitsBySub = groupBy(units, (u) => u.subzone_id)
      const coldBySub = groupBy(cold, (u) => u.subzone_id)
      const usedSubIds = new Set([...unitsBySub.keys(), ...coldBySub.keys()])
      const liveSubs = subzones.filter((s) => usedSubIds.has(s.id))
      const orphanSubs = Array.from(usedSubIds)
        .filter((id) => !subzones.some((s) => s.id === id))
        .map((id) => ({ id, name: '已解散小区', zone_ids: [], tags: [] }))
      const allSubs = [...liveSubs, ...orphanSubs]

      const R_ZONE = 400
      const R_CLUSTER = 170
      const R_STAR = 72

      zonesList.forEach((zone, zi) => {
        const solo = zonesList.length === 1
        const a = solo ? 0 : (zi / zonesList.length) * Math.PI * 2
        const zx = solo ? 0 : Math.cos(a) * R_ZONE
        const zy = solo ? 0 : Math.sin(a * 1.7) * 90
        const zz = solo ? 0 : Math.sin(a) * R_ZONE
        const tint = ZONE_TINT[zi % ZONE_TINT.length]
        const mySubs = allSubs.filter((s) => (s.zone_ids || []).includes(zone.id)
          || ((s.zone_ids || []).length === 0 && zi === 0))
        zones.push({ id: zone.id, name: zone.name, x: zx, y: zy, z: zz, r: R_CLUSTER * 1.25, tint })

        mySubs.forEach((sub, si) => {
          const hot = unitsBySub.get(sub.id) || []
          const frozen = coldBySub.get(sub.id) || []
          const off = mySubs.length <= 1 ? { x: 0, y: 0, z: 0 } : spherePoint(si, mySubs.length, R_CLUSTER)
          const sx = zx + off.x
          const sy = zy + off.y
          const sz = zz + off.z
          clusters.push({
            id: sub.id, name: sub.name, x: sx, y: sy, z: sz,
            r: 34 + Math.sqrt(hot.length + frozen.length) * 12,
            tint, hot: hot.length, cold: frozen.length,
          })

          const items = [
            ...hot.map((u) => ({ u, cold: false })),
            ...frozen.map((u) => ({ u, cold: true })),
          ]
          items.forEach((item, i) => {
            const p = items.length <= 1 ? { x: 0, y: 0, z: 0 } : spherePoint(i, items.length, R_STAR)
            const star = {
              u: item.u,
              cold: item.cold,
              x: sx + p.x,
              y: sy + p.y,
              z: sz + p.z,
              base: item.cold ? 3 : 3.4 + (item.u.score || 0.6) * 5,
              color: item.cold ? '#5c6b92' : (MATURITY_COLOR[item.u.maturity] || '#9fb6ff'),
            }
            stars.push(star)
            byId.set(item.u.id, star)
          })
        })
      })

      // 未归属小区：悬在下方，避免"数据消失"
      const drawn = new Set(clusters.map((c) => c.id))
      allSubs.filter((s) => !drawn.has(s.id)).forEach((sub, i) => {
        clusters.push({
          id: sub.id, name: sub.name, x: -260 + i * 130, y: -R_ZONE, z: 0,
          r: 30, tint: '#7a8ab8', hot: 0, cold: 0,
        })
      })

      // 冲突连线（三维）
      for (const u of units) {
        for (const cid of u.conflicts || []) {
          const a = byId.get(u.id)
          const b = byId.get(cid)
          if (a && b && !a.cold && !b.cold) links.push([a, b])
        }
      }

      // 待批提案 = 缓慢下坠的流星
      const meteors = (data.proposals || []).map((p) => ({
        p,
        x: -320 + pseudo(p.id, 'mx') * 640,
        z: -320 + pseudo(p.id, 'mz') * 640,
        phase: pseudo(p.id, 'ph') * 940,
        speed: 8 + pseudo(p.id, 'sp') * 12,
        color: KIND_COLOR[p.kind] || '#9fb6ff',
      }))

      // 背景星尘（球形）
      for (let i = 0; i < 260; i += 1) {
        const p = spherePoint(i, 260, 1200 + pseudo('dust' + i, 'r') * 500)
        dust.push({ x: p.x, y: p.y * 1.4, z: p.z, r: 0.5 + pseudo('dust' + i, 's') * 1.2, a: 0.06 + pseudo('dust' + i, 'a') * 0.2 })
      }

      // 场景包围球半径：相机距离据此自适应，保证任何数据量都完整入画
      let radius = 200
      for (const z of zones) radius = Math.max(radius, Math.hypot(z.x, z.y, z.z) + z.r)
      for (const c of clusters) radius = Math.max(radius, Math.hypot(c.x, c.y, c.z) + c.r * 2)
      for (const s of stars) radius = Math.max(radius, Math.hypot(s.x, s.y, s.z) + s.base * 4)

      return { stars, clusters, zones, links, dust, meteors, radius }
    }

    /** 透视投影：先绕 Y 偏航、再绕 X 俯仰，最后按相机距离做透视。 */
    function makeProjector(cam, width, height) {
      const cy = Math.cos(cam.yaw)
      const sy = Math.sin(cam.yaw)
      const cp = Math.cos(cam.pitch)
      const sp = Math.sin(cam.pitch)
      const ox = width / 2 + cam.panX
      const oy = height / 2 + cam.panY
      return (x, y, z) => {
        const x1 = x * cy - z * sy
        const z1 = x * sy + z * cy
        const y2 = y * cp - z1 * sp
        const z2 = y * sp + z1 * cp
        const depth = z2 + cam.dist
        if (depth <= 60) return null
        const k = cam.fov / depth
        return { px: ox + x1 * k, py: oy - y2 * k, depth, k }
      }
    }

    /** 画一帧，并把投影后的星点写进 out，供命中测试复用。 */
    function drawScene3D(canvas, scene, cam, hoverId, selectedId, out, now) {
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      const width = canvas.clientWidth
      const height = canvas.clientHeight
      if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
        canvas.width = Math.round(width * dpr)
        canvas.height = Math.round(height * dpr)
      }
      const g = canvas.getContext('2d')
      if (!g) return
      g.setTransform(dpr, 0, 0, dpr, 0, 0)
      g.clearRect(0, 0, width, height)

      const bg = g.createRadialGradient(width * 0.5, height * 0.46, 30, width * 0.5, height * 0.5, Math.max(width, height) * 0.8)
      bg.addColorStop(0, '#0a1128')
      bg.addColorStop(0.55, '#060a18')
      bg.addColorStop(1, '#03050c')
      g.fillStyle = bg
      g.fillRect(0, 0, width, height)

      const project = makeProjector(cam, width, height)
      out.length = 0

      // 天球网格：三条大圆 + 一条赤道，帮助感知三维
      const R = 620
      g.lineWidth = 1
      for (let ring = 0; ring < 3; ring += 1) {
        const tilt = (ring * Math.PI) / 3
        g.strokeStyle = 'rgba(110,140,220,0.07)'
        g.beginPath()
        for (let i = 0; i <= 72; i += 1) {
          const a = (i / 72) * Math.PI * 2
          const x = Math.cos(a) * R
          const y0 = Math.sin(a) * R
          const y = y0 * Math.cos(tilt) * 0.55
          const z = y0 * Math.sin(tilt)
          const p = project(x, y, z)
          if (!p) continue
          if (i === 0) g.moveTo(p.px, p.py)
          else g.lineTo(p.px, p.py)
        }
        g.stroke()
      }

      // 星尘
      for (const d of scene.dust) {
        const p = project(d.x, d.y, d.z)
        if (!p) continue
        g.globalAlpha = d.a * Math.min(1, 900 / p.depth)
        g.fillStyle = '#cfe0ff'
        g.beginPath()
        g.arc(p.px, p.py, d.r, 0, Math.PI * 2)
        g.fill()
      }
      g.globalAlpha = 1

      // 星云 + 星团光晕（按深度从远到近）
      const glows = []
      for (const z of scene.zones) {
        const p = project(z.x, z.y, z.z)
        if (p) glows.push({ p, r: z.r * p.k, tint: z.tint, kind: 'zone', name: z.name })
      }
      for (const c of scene.clusters) {
        const p = project(c.x, c.y, c.z)
        if (p) glows.push({ p, r: c.r * p.k, tint: c.tint, kind: 'cluster', name: c.name, hot: c.hot, cold: c.cold })
      }
      glows.sort((a, b) => b.p.depth - a.p.depth)
      for (const item of glows) {
        const alpha = Math.min(1, 900 / item.p.depth)
        const grad = g.createRadialGradient(item.p.px, item.p.py, 0, item.p.px, item.p.py, Math.max(item.r, 6))
        grad.addColorStop(0, hexA(item.tint, (item.kind === 'zone' ? 0.2 : 0.3) * alpha))
        grad.addColorStop(1, hexA(item.tint, 0))
        g.fillStyle = grad
        g.beginPath()
        g.arc(item.p.px, item.p.py, Math.max(item.r, 6), 0, Math.PI * 2)
        g.fill()
        if (item.kind === 'cluster') {
          g.strokeStyle = hexA(item.tint, 0.26 * alpha)
          g.setLineDash([3, 5])
          g.beginPath()
          g.arc(item.p.px, item.p.py, Math.max(item.r, 6), 0, Math.PI * 2)
          g.stroke()
          g.setLineDash([])
        }
      }

      // 冲突连线
      for (const [a, b] of scene.links) {
        const pa = project(a.x, a.y, a.z)
        const pb = project(b.x, b.y, b.z)
        if (!pa || !pb) continue
        const alpha = Math.min(1, 700 / Math.max(pa.depth, pb.depth))
        g.strokeStyle = `rgba(255,95,116,${0.45 * alpha})`
        g.lineWidth = 1.2
        g.setLineDash([4, 4])
        g.beginPath()
        g.moveTo(pa.px, pa.py)
        g.lineTo(pb.px, pb.py)
        g.stroke()
      }
      g.setLineDash([])

      // 星星：按深度从远到近绘制
      const projected = []
      for (const s of scene.stars) {
        const p = project(s.x, s.y, s.z)
        if (!p) continue
        projected.push({ star: s, p })
      }
      projected.sort((a, b) => b.p.depth - a.p.depth)

      for (const item of projected) {
        const s = item.star
        const p = item.p
        const near = Math.min(1, 900 / p.depth)
        const selected = selectedId === s.u.id
        const hovered = hoverId === s.u.id
        const radius = Math.max(1.2, s.base * p.k * (selected ? 1.7 : hovered ? 1.35 : 1))
        const glowR = radius * 5
        const glow = g.createRadialGradient(p.px, p.py, 0, p.px, p.py, glowR)
        glow.addColorStop(0, hexA(s.color, (s.cold ? 0.22 : 0.42) * near))
        glow.addColorStop(0.4, hexA(s.color, (s.cold ? 0.08 : 0.14) * near))
        glow.addColorStop(1, hexA(s.color, 0))
        g.fillStyle = glow
        g.beginPath()
        g.arc(p.px, p.py, glowR, 0, Math.PI * 2)
        g.fill()

        g.globalAlpha = (s.cold ? 0.5 : 0.95) * near
        g.fillStyle = s.color
        g.beginPath()
        g.arc(p.px, p.py, radius, 0, Math.PI * 2)
        g.fill()
        g.globalAlpha = 1

        if (s.u.pinned && !s.cold) {
          g.strokeStyle = `rgba(255,255,255,${0.85 * near})`
          g.lineWidth = 1.2
          g.beginPath()
          g.arc(p.px, p.py, radius + 3.5, 0, Math.PI * 2)
          g.stroke()
        }
        if (selected) {
          g.strokeStyle = '#fff'
          g.lineWidth = 1.6
          g.beginPath()
          g.arc(p.px, p.py, radius + 6, 0, Math.PI * 2)
          g.stroke()
        }

        out.push({ id: s.u.id, px: p.px, py: p.py, depth: p.depth, radius, kind: 'star' })
      }

      // 流星：待批提案在屏幕空间缓慢下坠（不随相机旋转，保证随时可点）
      const tt = (now || 0) / 1000
      const laneW = width * 0.86
      for (const m of scene.meteors || []) {
        const mx = width * 0.07 + pseudo(m.p.id, 'sx') * laneW
        const span = Math.max(220, height * 0.8)
        const y = height * 0.9 - (((tt * m.speed) + m.phase) % span)
        const tailY = y - 54
        const grad = g.createLinearGradient(mx, tailY, mx, y)
        grad.addColorStop(0, hexA(m.color, 0))
        grad.addColorStop(1, hexA(m.color, 0.85))
        g.strokeStyle = grad
        g.lineWidth = 2
        g.beginPath()
        g.moveTo(mx, tailY)
        g.lineTo(mx, y)
        g.stroke()

        const r = 3.8
        const glow = g.createRadialGradient(mx, y, 0, mx, y, r * 6)
        glow.addColorStop(0, hexA(m.color, 0.7))
        glow.addColorStop(1, hexA(m.color, 0))
        g.fillStyle = glow
        g.beginPath()
        g.arc(mx, y, r * 6, 0, Math.PI * 2)
        g.fill()
        g.fillStyle = m.color
        g.beginPath()
        g.arc(mx, y, r, 0, Math.PI * 2)
        g.fill()

        if (selectedId === m.p.id) {
          g.strokeStyle = '#fff'
          g.lineWidth = 1.6
          g.beginPath()
          g.arc(mx, y, r + 7, 0, Math.PI * 2)
          g.stroke()
        }
        out.push({
          id: m.p.id,
          px: mx,
          py: y,
          depth: 1,
          radius: 16,
          kind: 'meteor',
        })
      }

      // 名称（最后画，压在最上层）
      g.textAlign = 'center'
      for (const item of glows) {
        const alpha = Math.min(1, 900 / item.p.depth) * (item.kind === 'zone' ? 0.8 : 0.62)
        g.fillStyle = `rgba(200,214,246,${alpha})`
        g.font = item.kind === 'zone'
          ? '600 13px system-ui, "Segoe UI", sans-serif'
          : '11px system-ui, "Segoe UI", sans-serif'
        g.fillText(item.name, item.p.px, item.p.py - Math.max(item.r, 6) - 10)
        if (item.kind === 'cluster' && item.hot + item.cold > 0) {
          g.fillStyle = `rgba(150,170,215,${alpha * 0.8})`
          g.font = '10px system-ui, "Segoe UI", sans-serif'
          g.fillText(`${item.hot}${item.cold ? ` · 冷${item.cold}` : ''}`, item.p.px, item.p.py + Math.max(item.r, 6) + 14)
        }
      }
    }

    function Legend() {
      const items = [
        ['#7aa7ff', '待验证'],
        ['#3fd6a4', '已验证'],
        ['#ffd166', '高可信'],
        ['#ff5f74', '已翻车'],
        ['#5c6b92', '冷库'],
      ]
      return h('div', { className: 'dshmem-legend' },
        ...items.map(([color, label]) => h('span', { key: label },
          h('i', { style: { background: color } }), label)),
        h('span', { style: { color: '#8fa4d8' } }, '○ 白环 = 重要'),
      )
    }

    function DetailCard({ star, onClose, onReset }) {
      const u = star.u
      const rows = []
      if (u.source_ref) rows.push(h('div', { className: 'row' }, h('span', { className: 'k' }, '溯源 '), u.source_ref))
      if (u.premise) rows.push(h('div', { className: 'row' }, h('span', { className: 'k' }, '前提 '), u.premise))
      if (u.counter) rows.push(h('div', { className: 'row' }, h('span', { className: 'k' }, '坑/限 '), u.counter))
      if (u.conflicts && u.conflicts.length) {
        rows.push(h('div', { className: 'row' }, h('span', { className: 'k' }, '冲突 '), u.conflicts.join('、')))
      }
      return h('div', { className: 'dshmem-card' },
        h('button', { className: 'close', onClick: onClose, title: '关闭', type: 'button' }, '×'),
        h('h4', null, u.summary),
        h('div', { className: 'row' },
          h('span', { className: 'k' }, '状态 '),
          `${MATURITY_LABEL[u.maturity] || u.maturity} · ${TYPE_LABEL[u.case_type] || u.case_type} · 可信 ${u.score}`,
          u.pinned ? ' · 🔒重要' : '',
          star.cold ? ' · 冷库(只读)' : '',
        ),
        h('div', { className: 'row' }, h('span', { className: 'k' }, '单元 '), u.id),
        h('div', { className: 'row' }, h('span', { className: 'k' }, '时间 '), String(u.timestamp || '').replace('T', ' ').slice(0, 16)),
        ...rows,
        u.tags && u.tags.length
          ? h('div', { className: 'tags' }, ...u.tags.map((t) => h('span', { key: t, className: 'tag' }, t)))
          : null,
        h('div', { style: { marginTop: '12px' } },
          h('button', {
            className: 'dshmem-btn', type: 'button', onClick: onReset,
          }, '回到全景'),
        ),
      )
    }

    function StarMapSection() {
      const [payload, setPayload] = useState({ state: 'loading' })
      const [selectedId, setSelectedId] = useState(null)
      const [hover, setHover] = useState(null)
      const [autoRotate, setAutoRotate] = useState(true)
      const [size, setSize] = useState({ w: 0, h: 0 })
      const [meteorId, setMeteorId] = useState(null)
      const [actState, setActState] = useState({ busy: false, msg: '' })

      const canvasRef = useRef(null)
      const wrapRef = useRef(null)
      const camRef = useRef({ ...CAMERA_DEFAULT })
      const sceneRef = useRef(null)
      const projectedRef = useRef([])
      const dragRef = useRef(null)
      const flyRef = useRef(null)
      const insideRef = useRef(false)
      const frozenAtRef = useRef(0)
      const hoverRef = useRef(null)
      const selectedRef = useRef(null)
      const autoRef = useRef(true)
      const rafRef = useRef(0)
      const lastRef = useRef(0)

      const load = useCallback(async (force) => {
        setPayload((prev) => (prev.state === 'ready' && !force ? prev : { state: 'loading' }))
        try {
          const url = '/dsh-memory/starmap.json' + (force ? '?t=' + Date.now() : '')
          const res = await fetch(url, { cache: 'no-store' })
          const json = await res.json()
          if (json && json.ok === false) {
            setPayload({ state: 'error', message: json.error || '导出失败', dataDir: json.dataDir })
            return
          }
          setPayload({ state: 'ready', data: json })
        } catch (error) {
          setPayload({ state: 'error', message: String(error && error.message ? error.message : error) })
        }
      }, [])

      useEffect(() => { load(false) }, [load])

      useEffect(() => {
        if (!wrapRef.current) return undefined
        const el = wrapRef.current
        const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }))
        ro.observe(el)
        setSize({ w: el.clientWidth, h: el.clientHeight })
        return () => ro.disconnect()
      }, [])

      const scene = useMemo(() => {
        if (payload.state !== 'ready') return null
        return buildScene3D(payload.data)
      }, [payload])

      useEffect(() => { sceneRef.current = scene }, [scene])
      useEffect(() => { hoverRef.current = hover ? hover.id : null }, [hover])
      useEffect(() => { selectedRef.current = selectedId }, [selectedId])
      useEffect(() => { autoRef.current = autoRotate }, [autoRotate])

      // 自适应取景：按场景包围球与画布尺寸算出相机距离，保证整体入画
      useEffect(() => {
        if (!scene || !size.w || !size.h) return
        const fitK = (Math.min(size.w, size.h) * 0.62) / Math.max(scene.radius, 1)
        camRef.current.dist = CAMERA_DEFAULT.fov / Math.max(0.18, fitK)
      }, [scene, size])

      // 渲染循环：飞行/自动旋转/流星需要逐帧重绘
      useEffect(() => {
        const canvas = canvasRef.current
        if (!canvas) return undefined
        const tick = (now) => {
          rafRef.current = requestAnimationFrame(tick)
          const dt = lastRef.current ? Math.min(0.05, (now - lastRef.current) / 1000) : 0
          lastRef.current = now
          const current = sceneRef.current
          if (!current || !canvas.clientWidth) return
          if (flyRef.current) {
            const f = flyRef.current
            const t = Math.min(1, (now - f.t0) / f.dur)
            const e = 1 - Math.pow(1 - t, 3)
            camRef.current.yaw = f.from.yaw + shortestAngle(f.from.yaw, f.to.yaw) * e
            camRef.current.pitch = f.from.pitch + (f.to.pitch - f.from.pitch) * e
            camRef.current.dist = f.from.dist + (f.to.dist - f.from.dist) * e
            camRef.current.panX = f.from.panX + (f.to.panX - f.from.panX) * e
            camRef.current.panY = f.from.panY + (f.to.panY - f.from.panY) * e
            if (t >= 1) flyRef.current = null
          } else if (autoRef.current && !dragRef.current && !selectedRef.current) {
            camRef.current.yaw += dt * 0.16
          }
          drawScene3D(canvas, current, camRef.current, hoverRef.current, selectedRef.current, projectedRef.current, insideRef.current ? frozenAtRef.current : now)
        }
        rafRef.current = requestAnimationFrame(tick)
        return () => cancelAnimationFrame(rafRef.current)
      }, [])

      const pickAt = useCallback((clientX, clientY) => {
        const canvas = canvasRef.current
        if (!canvas) return null
        const rect = canvas.getBoundingClientRect()
        const mx = clientX - rect.left
        const my = clientY - rect.top
        let best = null
        let bestScore = Infinity
        for (const p of projectedRef.current) {
          const d = Math.hypot(p.px - mx, p.py - my)
          const hit = Math.max(8, p.radius + 6)
          if (d <= hit && d < bestScore) {
            bestScore = d
            best = p
          }
        }
        return best
      }, [])

      /** 点星 = 镜头飞过去聚焦（把该星转到画面中心并拉近）。 */
      const flyToStar = useCallback((star) => {
        const rXZ = Math.hypot(star.x, star.z)
        const yaw = Math.atan2(star.x, star.z)
        const pitch = Math.max(-1.2, Math.min(1.2, Math.atan2(star.y, Math.max(rXZ, 1))))
        const wantK = 72 / Math.max((star.base || 4) * 5, 1)
        const dist = Math.max(300, Math.min(1600, CAMERA_DEFAULT.fov / Math.max(0.3, wantK)))
        flyRef.current = {
          from: { ...camRef.current },
          to: { yaw, pitch, dist, panX: 0, panY: 0 },
          t0: performance.now(),
          dur: 680,
        }
      }, [])

      const act = useCallback(async (id, action) => {
        setActState({ busy: true, msg: '' })
        try {
          const res = await fetch('/dsh-memory/proposals', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id, action }),
          })
          const json = await res.json()
          if (json && json.ok) {
            setActState({ busy: false, msg: action === 'approve' ? '已批准并执行' : '已驳回' })
            setMeteorId(null)
            setSelectedId(null)
            load(true)
          } else {
            setActState({ busy: false, msg: (json && (json.error || json.message)) || '操作失败' })
          }
        } catch (error) {
          setActState({ busy: false, msg: String(error && error.message ? error.message : error) })
        }
      }, [load])

      const onPointerDown = (event) => {
        const hit = pickAt(event.clientX, event.clientY)
        if (hit) {
          if (hit.kind === 'meteor') {
            setMeteorId(hit.id)
            setSelectedId(null)
            setActState({ busy: false, msg: '' })
            return
          }
          setSelectedId(hit.id)
          setMeteorId(null)
          const star = sceneRef.current
            ? sceneRef.current.stars.find((s) => s.u.id === hit.id)
            : null
          if (star) flyToStar(star)
          return
        }
        dragRef.current = {
          x: event.clientX, y: event.clientY,
          yaw: camRef.current.yaw, pitch: camRef.current.pitch,
        }
        if (event.currentTarget && event.currentTarget.setPointerCapture) {
          try { event.currentTarget.setPointerCapture(event.pointerId) } catch (e) { /* ignore */ }
        }
        if (event.currentTarget && event.currentTarget.classList) event.currentTarget.classList.add('is-dragging')
      }

      const onPointerMove = (event) => {
        if (dragRef.current) {
          const dx = event.clientX - dragRef.current.x
          const dy = event.clientY - dragRef.current.y
          camRef.current.yaw = dragRef.current.yaw - dx * 0.006
          camRef.current.pitch = Math.max(-1.25, Math.min(1.25, dragRef.current.pitch - dy * 0.006))
          if (hover) setHover(null)
          return
        }
        const hit = pickAt(event.clientX, event.clientY)
        if (hit) {
          const rect = wrapRef.current.getBoundingClientRect()
          if (hit.kind === 'meteor') {
            const meteor = sceneRef.current
              ? (sceneRef.current.meteors || []).find((m) => m.p.id === hit.id)
              : null
            if (meteor) {
              setHover({
                id: hit.id,
                x: event.clientX - rect.left + 14,
                y: event.clientY - rect.top + 14,
                meteor,
              })
            }
            return
          }
          const star = sceneRef.current
            ? sceneRef.current.stars.find((s) => s.u.id === hit.id)
            : null
          if (star) {
            setHover({
              id: hit.id,
              x: event.clientX - rect.left + 14,
              y: event.clientY - rect.top + 14,
              star,
            })
          }
        } else if (hover) {
          setHover(null)
        }
      }

      const endDrag = (event) => {
        dragRef.current = null
        if (event && event.currentTarget) {
          if (event.currentTarget.releasePointerCapture) {
            try { event.currentTarget.releasePointerCapture(event.pointerId) } catch (e) { /* ignore */ }
          }
          if (event.currentTarget.classList) event.currentTarget.classList.remove('is-dragging')
        }
      }

      const onWheel = (event) => {
        event.preventDefault()
        const factor = event.deltaY < 0 ? 0.9 : 1.1
        camRef.current.dist = Math.max(320, Math.min(2600, camRef.current.dist * factor))
      }

      const resetView = () => {
        flyRef.current = null
        camRef.current = { ...CAMERA_DEFAULT }
        setSelectedId(null)
        setMeteorId(null)
      }

      const refresh = () => {
        resetView()
        load(true)
      }

      const stats = payload.state === 'ready' ? payload.data.stats : null
      const empty = payload.state === 'ready'
        && (payload.data.units || []).length === 0
        && (payload.data.cold || []).length === 0
        && (payload.data.proposals || []).length === 0
      const selected = selectedId && scene ? scene.stars.find((s) => s.u.id === selectedId) : null
      const meteor = meteorId && scene ? (scene.meteors || []).find((m) => m.p.id === meteorId) : null

      return h('div', { className: 'dshmem-root' },
        styleTag(),
        h('div', { className: 'dshmem-head' },
          h('div', null,
            h('h3', { className: 'dshmem-title' }, '记忆星图 · 3D'),
            h('p', { className: 'dshmem-sub' },
              '大区是星云、小区是星团、每条经验是一颗三维星点：亮度=可信度与热度，颜色=成熟度，白环=重要，红色虚线=冲突成对。拖拽旋转、滚轮缩放、双击复位。',
              h('br'),
              stats
                ? `共 ${stats.units} 颗经验星 · ${stats.cold} 颗冷库星 · ${stats.subzones} 个星团 · ${stats.pending} 条待批提案`
                : '正在读取记忆库…',
            ),
          ),
          h('div', { className: 'dshmem-actions' },
            h('button', {
              className: 'dshmem-btn' + (autoRotate ? ' on' : ''),
              type: 'button',
              onClick: () => setAutoRotate((v) => !v),
            }, autoRotate ? '自动旋转：开' : '自动旋转：关'),
            h('button', {
              className: 'dshmem-btn', type: 'button',
              onClick: refresh,
              disabled: payload.state === 'loading',
            }, payload.state === 'loading' ? '读取中…' : '刷新并复位'),
          ),
        ),
        h('div', { className: 'dshmem-stage', ref: wrapRef },
          h('canvas', {
            ref: canvasRef,
            className: 'dshmem-canvas',
            onPointerDown,
            onPointerMove,
            onPointerUp: endDrag,
            onPointerCancel: endDrag,
            onPointerEnter: () => { insideRef.current = true; frozenAtRef.current = performance.now() },
            onPointerLeave: (event) => {
              insideRef.current = false
              endDrag(event)
              setHover(null)
            },
            onWheel,
            onDoubleClick: resetView,
          }),
          stats ? h('div', { className: 'dshmem-hud' }, `待批 ${stats.pending} · 重要 ${stats.pinned}`) : null,
          scene ? h(Legend) : null,
          scene ? h('div', { className: 'dshmem-hint' }, '点星聚焦 · 点流星审批') : null,
          hover && hover.star ? h('div', {
            className: 'dshmem-tip',
            style: {
              left: Math.min(hover.x, Math.max(40, size.w - 320)),
              top: Math.min(hover.y, Math.max(40, size.h - 150)),
            },
          },
            h('b', null, hover.star.u.summary),
            h('div', null,
              h('span', { className: 'k' }, '成熟度 '),
              MATURITY_LABEL[hover.star.u.maturity] || hover.star.u.maturity,
              ` · 可信 ${hover.star.u.score}`,
              hover.star.cold ? ' · 冷库' : '',
            ),
            hover.star.u.premise ? h('div', null, h('span', { className: 'k' }, '前提 '), hover.star.u.premise) : null,
            hover.star.u.counter ? h('div', null, h('span', { className: 'k' }, '坑 '), hover.star.u.counter) : null,
            h('div', { style: { marginTop: '6px', color: '#8fa4d8' } }, '点击聚焦'),
          ) : null,
          hover && hover.meteor ? h('div', {
            className: 'dshmem-tip',
            style: {
              left: Math.min(hover.x, Math.max(40, size.w - 320)),
              top: Math.min(hover.y, Math.max(40, size.h - 150)),
            },
          },
            h('b', null, KIND_LABEL[hover.meteor.p.kind] || hover.meteor.p.kind),
            h('div', { style: { color: '#9fb0d8' } }, hover.meteor.p.reason || '（无原因说明）'),
            h('div', { style: { marginTop: '6px', color: '#8fa4d8' } }, '点击查看并审批'),
          ) : null,
          selected ? h(DetailCard, {
            star: selected,
            onClose: () => { setSelectedId(null); resetView() },
            onReset: resetView,
          }) : null,
          meteor ? h('div', { className: 'dshmem-card' },
            h('button', {
              className: 'close', type: 'button', title: '关闭',
              onClick: () => { setMeteorId(null); setActState({ busy: false, msg: '' }) },
            }, '×'),
            h('h4', null, (KIND_LABEL[meteor.p.kind] || meteor.p.kind) + ' · 待批'),
            h('div', { className: 'row' }, h('span', { className: 'k' }, '提案 '), meteor.p.id),
            h('div', { className: 'row' }, h('span', { className: 'k' }, '来源 '), meteor.p.plugin),
            h('div', { className: 'row' }, h('span', { className: 'k' }, '时间 '), String(meteor.p.created_at || '').replace('T', ' ').slice(0, 16)),
            h('div', { className: 'row' }, h('span', { className: 'k' }, '原因 '), meteor.p.reason || '—'),
            h('div', { style: { display: 'flex', gap: '8px', marginTop: '12px' } },
              h('button', {
                className: 'dshmem-btn', type: 'button',
                disabled: actState.busy,
                onClick: () => act(meteor.p.id, 'approve'),
              }, actState.busy ? '执行中…' : '批准并执行'),
              h('button', {
                className: 'dshmem-btn', type: 'button',
                disabled: actState.busy,
                onClick: () => act(meteor.p.id, 'reject'),
              }, '驳回'),
            ),
            actState.msg
              ? h('div', { style: { marginTop: '8px', fontSize: '12px', color: actState.msg.includes('已') ? '#7affc8' : '#ff9ea9' } }, actState.msg)
              : null,
          ) : null,
          empty ? h('div', { className: 'dshmem-empty' },
            h('div', { className: 'big' }, '这片星空还很安静'),
            h('div', null, '用 memory_save 记下第一条经验，星图上就会亮起第一颗星。'),
          ) : null,
          payload.state === 'error'
            ? h('div', { className: 'dshmem-err' },
              h('b', null, '读取记忆库失败'),
              h('div', null, payload.message),
              h('div', { style: { marginTop: '8px', color: '#ffb3bf' } },
                '提示：确认插件配置里的 dataDir 指向真实记忆库，且 pythonExe 可用。'),
            )
            : null,
        ),
      )
    }

    class StarMapBoundary extends React.Component {
      constructor(props) {
        super(props)
        this.state = { err: null }
      }
      static getDerivedStateFromError(err) {
        return { err }
      }
      render() {
        const err = this.state.err
        if (err) {
          return h('pre', {
            style: { padding: '20px', color: '#c0392b', whiteSpace: 'pre-wrap', fontSize: '12px' },
          }, 'STARMAP ERROR:\n' + (err.stack || err.message || String(err)))
        }
        return this.props.children
      }
    }

    function apply(ctx) {
      ctx.slots.inject('settings.section', function* () {
        yield ctx.slots.register({
          name: 'settings.section',
          id: 'dsh-memory-starmap',
          order: 60,
          label: () => '记忆星图',
        }, () => h(StarMapBoundary, null, h(StarMapSection)))
      })
    }

    return { apply, inject: ['slots'], name: 'dsh-memory-starmap' }
  },
})
