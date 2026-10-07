/* Beastborn web client - jQuery + Bootstrap.
 *
 * The server owns all game state (including the current selection). This file only:
 *   1. sends intents   (click / cancel / end_turn) and new-game requests,
 *   2. draws the state JSON it gets back,
 *   3. shows hover previews (path, move cost, exact damage) from that same state.
 */
$(function () {
  "use strict";

  const PLAYER_COLORS = ["#d6483f", "#3e7cde", "#e2b830", "#a056cc"];
  const SPRITES = new Set(["boss", "big_rat", "peasant"]); // files in /static/img/units/<type>.svg
  const MIN_TILE = 28, MAX_TILE = 64;

  let state = null;          // last state from the server
  let busy = false;          // a request is in flight
  let hover = null;          // {x, y} of the tile under the mouse
  let gameOverShown = null;  // game id for which the modal was already shown
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
          <span>ATK ${u.atk} × EN_ATK ${u.en_atk} = <strong>${u.atk * u.en_atk}</strong></span>
          <span>DEF ${def}</span>
          <span>Range ${u.range}</span>
          <span>Attack costs ${u.en_atk} EN</span>
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
