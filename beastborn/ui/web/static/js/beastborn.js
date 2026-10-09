/* Beastborn web client - jQuery + Bootstrap.
 *
 * The server owns all game state (including the current selection). This file only:
 *   1. sends intents   (click / cancel / end_turn) and new-game requests,
 *   2. draws the state JSON it gets back,
 *   3. shows hover previews (path, move cost, exact damage) from that same state,
 *   4. animates new attacks (arrow flight / melee lunge) before drawing the new state.
 */
$(function () {
  "use strict";

  const PLAYER_COLORS = ["#d6483f", "#3e7cde", "#e2b830", "#a056cc"];
  const SPRITES = new Set(["boss", "big_rat", "peasant", "archer"]); // files in /static/img/units/<type>.svg
  const MIN_TILE = 28, MAX_TILE = 64;

  let state = null;          // last state from the server
  let busy = false;          // a request is in flight
  let hover = null;          // {x, y} of the tile under the mouse
  let gameOverShown = null;  // game id for which the modal was already shown
  let seenSeq = 0;           // last action already shown; only newer attacks are animated
  const gameOverModal = new bootstrap.Modal("#game-over-modal");

  // ------------------------------------------------------------------ API
  const api = {
    create: (body) => request("POST", "/api/games", body),
    get: (id) => request("GET", `/api/games/${encodeURIComponent(id)}`),
    intent: (id, body) => request("POST", `/api/games/${encodeURIComponent(id)}/intents`, body),
  };

  function request(method, url, body) {
    return $.ajax({
      method,
      url,
      contentType: body ? "application/json" : undefined,
      data: body ? JSON.stringify(body) : undefined,
      dataType: "json",
    });
  }

  function errorText(xhr) {
    const detail = xhr.responseJSON && xhr.responseJSON.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) return detail[0].msg;
    return xhr.status ? `Server error (${xhr.status})` : "Cannot reach the server";
  }

  // ------------------------------------------------------------------ helpers
  const esc = (text) => $("<div>").text(String(text)).html();
  const color = (player) => PLAYER_COLORS[player % PLAYER_COLORS.length];
  const key = (x, y) => `${x},${y}`;
  const pct = (value, max) => Math.max(0, Math.min(100, Math.round((100 * value) / max)));

  function spriteHtml(unit, extraClass = "") {
    if (SPRITES.has(unit.type)) {
      return `<span class="sprite ${extraClass}" style="--sprite:url(/static/img/units/${unit.type}.svg)"></span>`;
    }
    return `<span class="letter">${esc(unit.code)}</span>`;
  }

  function toast(text, kind = "danger") {
    const $t = $(`
      <div class="toast align-items-center text-bg-${kind} border-0" role="alert">
        <div class="d-flex">
          <div class="toast-body">${esc(text)}</div>
          <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
        </div>
      </div>`);
    $("#toasts").append($t);
    $t.on("hidden.bs.toast", () => $t.remove());
    new bootstrap.Toast($t[0], { delay: 2500 }).show();
  }

  function setBusy(value) {
    busy = value;
    $("body").toggleClass("busy", value);
    $("#btn-start").prop("disabled", value);
  }

  // ------------------------------------------------------------------ screens
  function showStart() {
    state = null;
    hover = null;
    history.replaceState(null, "", "/");
    $("#game-screen").addClass("d-none");
    $("#start-screen").removeClass("d-none");
    $("#game-badge, #btn-copy-link").addClass("d-none");
  }

  function showGame() {
    $("#start-screen").addClass("d-none");
    $("#game-screen").removeClass("d-none");
    $("#game-badge").text(state.game_id.slice(0, 8)).attr("title", `Game ${state.game_id}`).removeClass("d-none");
    $("#btn-copy-link").removeClass("d-none");
  }

  function setState(newState) {
    state = newState;
    seenSeq = state.actions.seq;
    if (new URLSearchParams(location.search).get("game") !== state.game_id) {
      history.replaceState(null, "", `/?game=${state.game_id}`);
    }
    showGame();
    render();
    if (state.message.error && state.message.text) toast(state.message.text);
    if (state.phase === "game_over" && gameOverShown !== state.game_id) {
      gameOverShown = state.game_id;
      showGameOver();
    }
  }

  // ------------------------------------------------------------------ actions
  function startGame() {
    if (busy) return;
    const seed = $("#seed").val();
    const body = {
      players: Number($("input[name=players]:checked").val()),
      size: Number($("#size").val()),
      seed: seed === "" ? null : Number(seed),
    };
    setBusy(true);
    api.create(body)
      .done(setState)
      .fail((xhr) => toast(errorText(xhr)))
      .always(() => setBusy(false));
  }

  function loadGame(id) {
    setBusy(true);
    api.get(id)
      .done(setState)
      .fail((xhr) => {
        toast(xhr.status === 404 ? "That game has expired or does not exist." : errorText(xhr), "warning");
        showStart();
      })
      .always(() => setBusy(false));
  }

  function sendIntent(body) {
    if (busy || !state) return;
    setBusy(true);
    api.intent(state.game_id, body)
      .then((newState) => playAttacks(newAttacks(newState)).then(() => newState))
      .done(setState)
      .fail((xhr) => {
        toast(errorText(xhr));
        if (xhr.status === 404) showStart();
      })
      .always(() => setBusy(false));
  }

  // ------------------------------------------------------------------ rendering
  function tileSize() {
    const w = state.board.width, h = state.board.height;
    const byWidth = Math.floor(($("#board-wrap").width() - 4) / w);
    const byHeight = Math.floor((window.innerHeight - 150) / h);
    return Math.max(MIN_TILE, Math.min(MAX_TILE, byWidth, byHeight));
  }

  function render() {
    renderBoard();
    renderTurn();
    renderSelected();
    renderHover();
    renderLog();
    $("#btn-end-turn").prop("disabled", state.phase === "game_over" || !state.human_turn);
  }

  function renderBoard() {
    const { width, height, tiles } = state.board;
    const unitsAt = new Map(state.units.map((u) => [key(u.x, u.y), u]));
    const reach = new Map(state.selection.reachable.map((r) => [key(r.x, r.y), r]));
    const targets = new Set(state.selection.targets.map((t) => t.unit_id));
    const selected = state.selection.unit_id;

    const html = [];
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const tile = tiles[y][x];
        const unit = unitsAt.get(key(x, y));
        const r = reach.get(key(x, y));
        const classes = ["tile", `terrain-${tile.terrain}`, `elev-${tile.elevation}`];
        if (r) classes.push("reachable");
        if (unit && unit.id === selected) classes.push("selected");
        if (unit && targets.has(unit.id)) classes.push("target");

        let inner = "";
        if (tile.elevation > 0) inner += `<span class="elev-pips">${"<i></i>".repeat(tile.elevation)}</span>`;
        if (r) inner += `<span class="cost">${r.cost}</span>`;
        if (unit) inner += unitHtml(unit);
        html.push(`<div class="${classes.join(" ")}" data-x="${x}" data-y="${y}">${inner}</div>`);
      }
    }
    $("#board")
      .css({ "--cols": width, "--tile": `${tileSize()}px` })
      .html(html.join(""));
    applyHover();
  }

  function unitHtml(u) {
    const classes = ["unit"];
    if (u.boss) classes.push("boss");
    if (u.owner !== state.active_player) classes.push("inactive");
    const venom = u.effects.some((e) => e.kind === "venom") ? '<span class="badge-effect"></span>' : "";
    return `
      <div class="${classes.join(" ")}" style="--team:${color(u.owner)}">
        <div class="disc">${spriteHtml(u)}</div>
        ${venom}
        <div class="bars">
          <div class="bar"><span style="width:${pct(u.hp, u.max_hp)}%"></span></div>
          <div class="bar en"><span style="width:${pct(u.energy, u.max_en)}%"></span></div>
        </div>
      </div>`;
  }

  function renderTurn() {
    const active = state.players[state.active_player];
    $("#turn-info").html(
      `<span class="player-chip active" style="--team:${color(active.index)}"><span class="dot"></span>${esc(active.name)}</span>` +
      ` <span class="ms-1">Round ${state.round}</span>`
    );
    $("#seed-info").text(state.seed === null ? "" : `seed ${state.seed}`);
    $("#players").html(state.players.map((p) => {
      const cls = ["player-chip", p.eliminated ? "eliminated" : ""].join(" ");
      const info = p.eliminated ? "out" : `${p.units} unit${p.units === 1 ? "" : "s"}`;
      return `<span class="${cls}" style="--team:${color(p.index)}"><span class="dot"></span>${esc(p.name)}: ${info}</span>`;
    }).join(""));
  }

  function unitCard(u, compact = false) {
    const def = u.current_def !== u.def
      ? `${u.current_def} <span class="text-secondary">(base ${u.def}, terrain)</span>`
      : `${u.def}`;
    const effects = u.effects.map((e) => `<span class="badge text-bg-success">${esc(e.kind)} ${e.magnitude} · ${e.turns}t</span>`).join(" ");
    const onHit = u.on_hit.map((k) => `<span class="badge text-bg-dark border">on hit: ${esc(k)}</span>`).join(" ");
    return `
      <div class="unit-card">
        <div class="d-flex align-items-center gap-2 mb-1">
          <span style="--team:${color(u.owner)}">${SPRITES.has(u.type) ? spriteHtml(u, "fs-3") : esc(u.code)}</span>
          <strong>${esc(u.name)}</strong>
          <span class="badge" style="background:${color(u.owner)}">P${u.owner + 1}</span>
          ${u.boss ? '<span class="badge text-bg-warning">Boss</span>' : ""}
        </div>
        <div class="d-flex align-items-center gap-2 small">
          <i class="bi bi-heart-fill text-danger"></i>
          <div class="progress flex-grow-1 stat-bar"><div class="progress-bar bg-success" style="width:${pct(u.hp, u.max_hp)}%"></div></div>
          <span class="text-nowrap">${u.hp}/${u.max_hp}</span>
        </div>
        <div class="d-flex align-items-center gap-2 small mb-1">
          <i class="bi bi-lightning-charge-fill text-info"></i>
          <div class="progress flex-grow-1 stat-bar"><div class="progress-bar bg-info" style="width:${pct(u.energy, u.max_en)}%"></div></div>
          <span class="text-nowrap">${u.energy}/${u.max_en} <span class="text-secondary">+${u.reg_en}</span></span>
        </div>
        ${compact ? "" : `
        <div class="stats small">
          <span>ATK <strong>${u.atk}</strong></span>
          <span>DEF ${def}</span>
          <span>Range ${u.range}${u.range > 1 ? ' <i class="bi bi-bullseye" title="Ranged: damage depends on distance"></i>' : ""}</span>
          <span>Attack costs ${u.en_atk} EN</span>
          ${u.move_penalty ? `<span class="text-warning">Slow: +${u.move_penalty} EN per step</span>` : ""}
        </div>`}
        <div class="mt-1">${effects} ${onHit}</div>
      </div>`;
  }

  function renderSelected() {
    const u = state.units.find((unit) => unit.id === state.selection.unit_id);
    if (!u) {
      $("#selected-info").html(
        state.phase === "game_over"
          ? '<span class="text-secondary">Game over.</span>'
          : '<span class="text-secondary">Click one of your units.</span>'
      );
      return;
    }
    const moves = state.selection.reachable.length;
    const targets = state.selection.targets.length;
    $("#selected-info").html(
      unitCard(u) +
      `<div class="small text-secondary mt-1">${moves} reachable tiles · ${targets} target${targets === 1 ? "" : "s"} in range</div>`
    );
  }

  function renderHover() {
    if (!hover || !state) {
      $("#hover-info").html('<span class="text-secondary">Move the mouse over the board.</span>');
      return;
    }
    const tile = state.board.tiles[hover.y][hover.x];
    const unit = state.units.find((u) => u.x === hover.x && u.y === hover.y);
    const r = state.selection.reachable.find((p) => p.x === hover.x && p.y === hover.y);
    const t = unit && state.selection.targets.find((target) => target.unit_id === unit.id);

    let html = `<div class="text-secondary">Tile (${hover.x},${hover.y}) · ${esc(tile.terrain)} · elevation ${tile.elevation}</div>`;
    if (r) html += `<div><i class="bi bi-signpost-2"></i> Move cost <strong>${r.cost} EN</strong></div>`;
    if (t) {
      const hpAfter = Math.max(0, unit.hp - t.damage);
      html += `<div class="text-danger"><i class="bi bi-crosshair"></i> Attack: ${esc(t.formula)} → <strong>${t.damage}</strong> dmg, HP ${unit.hp} → ${hpAfter}</div>`;
    }
    if (unit && unit.id !== state.selection.unit_id) html += `<div class="mt-1">${unitCard(unit, true)}</div>`;
    $("#hover-info").html(html);
  }

  function renderLog() {
    const $log = $("#log");
    $log.html(state.log.map((line) => {
      const turn = line.startsWith("---");
      return `<li class="list-group-item ${turn ? "turn-line" : ""}">${esc(turn ? line.replace(/-/g, "").trim() : line)}</li>`;
    }).join(""));
    $log.scrollTop($log[0].scrollHeight);
  }

  /** Hover effects without a full re-render: path highlight + damage popup. */
  function applyHover() {
    const $board = $("#board");
    $board.find(".on-path").removeClass("on-path");
    $board.find(".hovered").removeClass("hovered");
    $board.find(".dmg-pop").remove();
    if (!hover || !state) return;

    $board.find(`.tile[data-x=${hover.x}][data-y=${hover.y}]`).addClass("hovered");
    const r = state.selection.reachable.find((p) => p.x === hover.x && p.y === hover.y);
    if (r) {
      r.path.slice(1).forEach(([x, y]) => $board.find(`.tile[data-x=${x}][data-y=${y}]`).addClass("on-path"));
    }
    const unit = state.units.find((u) => u.x === hover.x && u.y === hover.y);
    const t = unit && state.selection.targets.find((target) => target.unit_id === unit.id);
    if (t) {
      $board.find(`.tile[data-x=${hover.x}][data-y=${hover.y}]`).append(`<span class="dmg-pop">-${t.damage}</span>`);
    }
  }

  // ------------------------------------------------------------------ attack animations
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const ARROW_SVG = `
    <svg viewBox="0 0 64 16" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M3 3 L14 8 L3 13 L7 8 Z M9 3 L20 8 L9 13 L13 8 Z" fill="currentColor"/>
      <rect x="8" y="7" width="44" height="2" rx="1" fill="#e9d8a6"/>
      <path d="M64 8 L50 2 L53 8 L50 14 Z" fill="#d9dee3" stroke="#5b6670" stroke-width="0.8"/>
    </svg>`;

  const newAttacks = (s) => s.actions.attacks.filter((a) => a.seq > seenSeq);
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const speed = (ms) => (REDUCED_MOTION ? Math.min(ms, 120) : ms);

  function run(el, keyframes, options) {
    if (!el || !el.animate) return wait(options.duration || 0);
    return el.animate(keyframes, options).finished.catch(() => {});
  }

  /** Centre of a board tile in #fx coordinates (the overlay inside #board-wrap). */
  function tileCenter([x, y]) {
    const wrap = $("#board-wrap")[0];
    const tile = $(`#board .tile[data-x=${x}][data-y=${y}]`)[0];
    if (!tile) return null;
    const w = wrap.getBoundingClientRect(), r = tile.getBoundingClientRect();
    return { x: r.left - w.left + wrap.scrollLeft + r.width / 2, y: r.top - w.top + wrap.scrollTop + r.height / 2, size: r.width };
  }

  const unitDisc = ([x, y]) => $(`#board .tile[data-x=${x}][data-y=${y}] .unit .disc`)[0];
  const at = (p, dx = 0, dy = 0) => `translate(${p.x + dx}px, ${p.y + dy}px) translate(-50%, -50%)`;

  /** Plays the attacks one after another. Never rejects: a failed animation must not block the game. */
  async function playAttacks(attacks) {
    if (!attacks.length || !state) return;
    $("#board .dmg-pop").remove();
    for (const a of attacks) {
      try {
        if (a.ranged) await shoot(a); else await strike(a);
      } catch (err) {
        console.warn("animation skipped", err);
      }
    }
  }

  /** Ranged: the shooter draws, an arrow flies along an arc and sticks in the target. */
  async function shoot(a) {
    const from = tileCenter(a.from), to = tileCenter(a.to);
    if (!from || !to) return;
    await run(unitDisc(a.from), [
      { transform: "translate(-50%, -50%) scale(1)" },
      { transform: "translate(-50%, -50%) scale(0.86)", offset: 0.6 },
      { transform: "translate(-50%, -50%) scale(1.06)" },
    ], { duration: speed(170), easing: "ease-out" });

    const dx = to.x - from.x, dy = to.y - from.y;
    const length = Math.hypot(dx, dy);
    const lift = Math.min(length * 0.3, from.size * 1.3); // arc height: lob over the tiles in between
    const frames = [];
    for (let i = 0, steps = 24; i <= steps; i++) {
      const t = i / steps;
      const x = from.x + dx * t, y = from.y + dy * t - lift * 4 * t * (1 - t);
      const angle = (Math.atan2(dy - lift * 4 * (1 - 2 * t), dx) * 180) / Math.PI;
      frames.push({ transform: `${at({ x, y })} rotate(${angle}deg)`, offset: t });
    }
    const $arrow = $(`<div class="fx-arrow" style="--team:${color(a.owner)}; width:${Math.round(from.size * 0.85)}px">${ARROW_SVG}</div>`)
      .appendTo("#fx");
    await run($arrow[0], frames, { duration: speed(240 + length * 1.6), easing: "cubic-bezier(.3,.1,.7,1)", fill: "forwards" });
    run($arrow[0], [{ opacity: 1 }, { opacity: 0 }], { duration: 320, delay: 120, fill: "forwards" }).then(() => $arrow.remove());
    await impact(a, to);
  }

  /** Melee: the attacker lunges at the target and back. */
  async function strike(a) {
    const from = tileCenter(a.from), to = tileCenter(a.to);
    if (!from || !to) return;
    const dx = (to.x - from.x) * 0.45, dy = (to.y - from.y) * 0.45;
    const disc = unitDisc(a.from);
    if (disc) disc.style.zIndex = 6;
    const lunge = run(disc, [
      { transform: "translate(-50%, -50%)" },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(1.1)`, offset: 0.45 },
      { transform: "translate(-50%, -50%)" },
    ], { duration: speed(300), easing: "ease-in-out" });
    await wait(speed(135));
    const hit = impact(a, to);
    await Promise.all([lunge, hit]);
  }

  /** Flash + shake on the target, and a floating damage number that survives the redraw. */
  async function impact(a, to) {
    const $burst = $('<div class="fx-burst"></div>').css({ width: to.size, height: to.size }).appendTo("#fx");
    run($burst[0], [
      { transform: `${at(to)} scale(0.3)`, opacity: 0.95 },
      { transform: `${at(to)} scale(1.15)`, opacity: 0 },
    ], { duration: speed(380), easing: "ease-out", fill: "forwards" }).then(() => $burst.remove());

    const text = a.damage > 0 ? `-${a.damage}` : "0";
    const $dmg = $(`<div class="fx-dmg ${a.damage > 0 ? "" : "zero"}">${text}${a.killed ? ' <i class="bi bi-x-octagon-fill"></i>' : ""}</div>`)
      .appendTo("#fx");
    const rise = Math.max(0, Math.min(to.size * 0.95, to.y - 12)); // stay inside the board on the top row
    run($dmg[0], [
      { transform: at(to, 0, -rise * 0.2), opacity: 0 },
      { transform: at(to, 0, -rise * 0.5), opacity: 1, offset: 0.2 },
      { transform: at(to, 0, -rise), opacity: 0 },
    ], { duration: REDUCED_MOTION ? 700 : 1100, easing: "ease-out", fill: "forwards" }).then(() => $dmg.remove());

    await run(unitDisc(a.to), [
      { transform: "translate(-50%, -50%)", filter: "brightness(1)" },
      { transform: "translate(calc(-50% - 4px), -50%)", filter: "brightness(2.2)", offset: 0.25 },
      { transform: "translate(calc(-50% + 4px), -50%)", filter: "brightness(1.6)", offset: 0.55 },
      { transform: "translate(-50%, -50%)", filter: "brightness(1)" },
    ], { duration: speed(260), easing: "ease-out" });
    await wait(speed(120));
  }

  function showGameOver() {
    const winner = state.winner;
    if (winner === null) {
      $("#winner-text").text("Draw");
      $("#winner-sprite").attr("style", "");
    } else {
      $("#winner-text").text(`${state.players[winner].name} wins!`);
      $("#winner-sprite").attr("style", `--sprite:url(/static/img/units/boss.svg); --team:${color(winner)}`);
    }
    $("#winner-subtext").text(`After ${state.round} round${state.round === 1 ? "" : "s"}.`);
    gameOverModal.show();
  }

  // ------------------------------------------------------------------ events
  $("#start-form").on("submit", (e) => { e.preventDefault(); startGame(); });
  $("#btn-new-game").on("click", showStart);
  $("#btn-modal-new-game").on("click", () => { gameOverModal.hide(); showStart(); });
  $("#btn-end-turn").on("click", () => sendIntent({ type: "end_turn" }));
  $("#btn-copy-link").on("click", () => {
    navigator.clipboard.writeText(location.href)
      .then(() => toast("Link copied - open it anywhere to continue this game.", "success"))
      .catch(() => toast(location.href, "secondary"));
  });

  $("#board")
    .on("click", ".tile", function () {
      sendIntent({ type: "click", x: Number(this.dataset.x), y: Number(this.dataset.y) });
    })
    .on("contextmenu", (e) => { e.preventDefault(); sendIntent({ type: "cancel" }); })
    .on("mouseenter", ".tile", function () {
      hover = { x: Number(this.dataset.x), y: Number(this.dataset.y) };
      applyHover();
      renderHover();
    })
    .on("mouseleave", () => { hover = null; applyHover(); renderHover(); });

  $(document).on("keydown", (e) => {
    if (!state || $(e.target).is("input, select, textarea") || $(".modal.show").length) return;
    if (e.key === "e" || e.key === "E" || e.key === " ") { e.preventDefault(); sendIntent({ type: "end_turn" }); }
    if (e.key === "Escape") sendIntent({ type: "cancel" });
  });

  let resizeTimer = null;
  $(window).on("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => state && renderBoard(), 120);
  });

  // ------------------------------------------------------------------ boot
  const gameId = new URLSearchParams(location.search).get("game");
  if (gameId) loadGame(gameId); else showStart();
});
