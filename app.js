"use strict";

const STORAGE_EXTRATO = "alvo_card_extrato_v1"; // legado (migrado para Vem Benefícios)
const STORAGE_EXTRATOS = "alvo_card_extratos_v2";
const STORAGE_RETORNO = "alvo_card_retorno_v1";
const STORAGE_SESSION = "alvo_card_session_v1";
const STORAGE_EXTRATO_SNAPSHOT = "alvo_card_extrato_snapshot_v1";

// AlvoCard, EiCard e Juntos Card temporariamente removidos — reativar adicionando de volta ao array
const ORIGINADORES = ["Vem Benefícios"];
const ORIGINADOR_RETORNO = "Vem Benefícios"; // única origem cruzada com o BPO

/* ---------------- Supabase ---------------- */

const SUPABASE_URL = "https://omwfowrgvlnjeirdjedm.supabase.co";
const SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9td2Zvd3JndmxuamVpcmRqZWRtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ2NTkxNDAsImV4cCI6MjEwMDIzNTE0MH0.U406AsWAqN-Uqgaf5NiTuovig6d89OQHEr6uuzaI4Jw";
const sb = supabase.createClient(SUPABASE_URL, SUPABASE_KEY);

async function sbSave(key, value) {
  const { error } = await sb
    .from("app_state")
    .upsert({ key, value, updated_at: new Date().toISOString() }, { onConflict: "key" });
  if (error) throw new Error(error.message);
}

async function sbLoad(key) {
  const { data, error } = await sb
    .from("app_state")
    .select("value")
    .eq("key", key)
    .maybeSingle();
  if (error) { console.error("Supabase load error:", key, error.message); return null; }
  console.log("Supabase load:", key, data ? "found" : "null");
  return data?.value ?? null;
}

async function sbDelete(key) {
  const { error } = await sb.from("app_state").delete().eq("key", key);
  if (error) console.error("Supabase delete error:", error.message);
}

const USERS = {
  Master: { password: "Upl@conc26", role: "master", label: "Master" },
  AlvoCard: { password: "@Conc2026", role: "viewer", label: "AlvoCard" },
};

let extratosPorOriginador = {}; // { [originador]: { convCol, dataCol, headers, rows, importadoEm } | null }
ORIGINADORES.forEach((o) => (extratosPorOriginador[o] = null));
let retornoData = null; // { rows: [{convenio, competencia, valor}] }

function getExtratoRows(originadorFiltro) {
  const lista = originadorFiltro ? [originadorFiltro] : ORIGINADORES;
  let rows = [];
  for (const o of lista) {
    const d = extratosPorOriginador[o];
    if (d && d.rows && d.rows.length) rows = rows.concat(d.rows);
  }
  return rows;
}

function hasAnyExtrato() {
  return ORIGINADORES.some((o) => extratosPorOriginador[o] && extratosPorOriginador[o].rows.length);
}

function countExtratoLancamentos() {
  return ORIGINADORES.reduce((acc, o) => acc + (extratosPorOriginador[o]?.rows.length || 0), 0);
}

function latestExtratoImportadoEm() {
  const datas = ORIGINADORES.map((o) => extratosPorOriginador[o]?.importadoEm).filter(Boolean);
  return datas.length ? new Date(Math.max(...datas.map((d) => new Date(d).getTime()))) : null;
}

const TOLERANCIA = 0.01;

const MESES_PT = {
  jan: 1, fev: 2, mar: 3, abr: 4, mai: 5, jun: 6,
  jul: 7, ago: 8, set: 9, out: 10, nov: 11, dez: 12,
};

const CHART_COLORS = {
  accent:       "#3B82F6",
  accentLight:  "#60A5FA",
  success:      "#10B981",
  successLight: "#34D399",
  teal:         "#0D9488",
  warning:      "#F59E0B",
  warningLight: "#FBBF24",
  danger:       "#EF4444",
  textDim:      "#94A3B8",
  tick:         "#64748B",
  grid:         "#1E293B",
};

if (typeof Chart !== "undefined") {
  Chart.defaults.color = CHART_COLORS.tick;
  Chart.defaults.font.family = "'Inter', sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.borderColor = CHART_COLORS.grid;
  Chart.defaults.plugins.tooltip.backgroundColor = "#111827";
  Chart.defaults.plugins.tooltip.titleColor = "#F1F5F9";
  Chart.defaults.plugins.tooltip.bodyColor = "#94A3B8";
  Chart.defaults.plugins.tooltip.borderColor = "#3B82F6";
  Chart.defaults.plugins.tooltip.borderWidth = 1;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 6;
  Chart.defaults.plugins.tooltip.titleFont = { family: "'Inter', sans-serif", weight: "600", size: 12 };
  Chart.defaults.plugins.tooltip.bodyFont = { family: "'JetBrains Mono', monospace", size: 12 };

  Chart.register({
    id: "centerText",
    afterDraw(chart) {
      const opts = chart.config.options?.plugins?.centerText;
      if (!opts) return;
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const cx = (chartArea.left + chartArea.right) / 2;
      const cy = (chartArea.top + chartArea.bottom) / 2;
      const hasLabel = !!opts.label;
      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillStyle = opts.color || "#F1F5F9";
      ctx.font = `700 ${opts.fontSize || 17}px Inter, sans-serif`;
      ctx.fillText(opts.value, cx, hasLabel ? cy - 11 : cy);
      if (hasLabel) {
        ctx.font = `500 11px Inter, sans-serif`;
        ctx.fillStyle = "#94A3B8";
        ctx.fillText(opts.label, cx, cy + 12);
      }
      ctx.restore();
    },
  });
}

let chartCreditoDebito = null;
let chartTopConvenios = null;
let chartEvolucao = null;
let chartConciliacaoStatus = null;
let chartConciliacaoComparativo = null;
let chartHomeConciliacaoStatus = null;

/* ---------------- Helpers ---------------- */

function slugOriginador(o) {
  return normalizeNome(o).replace(/\s+/g, "-");
}

function normalizeNome(s) {
  return String(s ?? "")
    .normalize("NFD")
    .replace(new RegExp("[\\u0300-\\u036f]", "g"), "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, " ");
}

// "abr/26" -> "04/2026"
function mesAnoProximoMes(mesAno) {
  if (!mesAno) return null;
  const [m, a] = mesAno.split("/").map(Number);
  const data = new Date(a, m - 1 + 1, 1);
  return `${String(data.getMonth() + 1).padStart(2, "0")}/${data.getFullYear()}`;
}

function competenciaParaMesAno(competencia) {
  if (!competencia) return null;
  const partes = String(competencia).trim().toLowerCase().split("/");
  if (partes.length !== 2) return null;

  const mesStr = partes[0].trim();
  let mes;
  if (/^\d+$/.test(mesStr)) {
    mes = parseInt(mesStr, 10);
    if (mes < 1 || mes > 12) return null;
  } else {
    mes = MESES_PT[mesStr.slice(0, 3)];
    if (!mes) return null;
  }

  let ano = partes[1].trim();
  if (ano.length === 2) ano = (ano >= "70" ? "19" : "20") + ano;
  if (ano.length !== 4) return null;

  return `${String(mes).padStart(2, "0")}/${ano}`;
}

// A data de movimentação chega como célula de data (Date) ou como texto —
// ISO ("2026-04-16") ou brasileiro ("10/08/2026 00:00:00"). O `new Date(texto)`
// lê DD/MM como MM/DD: "10/08/2026" viraria outubro e "21/07/2026" viraria
// Invalid Date, sumindo da conciliação. Por isso o formato BR é lido por
// máscara explícita, nunca pelo parser do motor.
const PADRAO_DATA_BR = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/;

// O extrato do Arbi usa 31/12/1899 como marcador de dia sem movimentação.
// Nenhum lançamento real é anterior a este ano.
const ANO_MINIMO_MOVIMENTO = 2000;

function parsearDataMovimento(valor) {
  if (valor instanceof Date) {
    return isNaN(valor.getTime()) || valor.getFullYear() < ANO_MINIMO_MOVIMENTO ? null : valor;
  }
  if (valor === null || valor === undefined) return null;

  const texto = String(valor).trim();
  if (!texto) return null;

  const partesBR = texto.match(PADRAO_DATA_BR);
  const data = partesBR
    ? new Date(Number(partesBR[3]), Number(partesBR[2]) - 1, Number(partesBR[1]))
    : new Date(texto);

  if (isNaN(data.getTime()) || data.getFullYear() < ANO_MINIMO_MOVIMENTO) return null;

  // Rejeita data inexistente que o Date "corrige" por transbordo (31/02 -> 03/03).
  if (partesBR && data.getDate() !== Number(partesBR[1])) return null;

  return data;
}

function moeda(v) {
  const n = Number(v) || 0;
  return "R$ " + n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function moedaCompacta(v) {
  const n = Number(v) || 0;
  const sinal = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  const formatar = (valor, sufixo) => {
    const arredondado = Math.round(valor * 10) / 10;
    const texto = Number.isInteger(arredondado) ? arredondado.toFixed(0) : arredondado.toFixed(1);
    return `${sinal}${texto.replace(".", ",")}${sufixo}`;
  };
  if (abs >= 1e9) return formatar(abs / 1e9, "B");
  if (abs >= 1e6) return formatar(abs / 1e6, "M");
  if (abs >= 1e3) return formatar(abs / 1e3, "K");
  return `${sinal}${abs.toLocaleString("pt-BR")}`;
}

function parseNumeroBR(v) {
  if (typeof v === "number") return v;
  if (v == null) return 0;
  let s = String(v).trim();
  if (!s) return 0;
  s = s.replace(/[^\d,.-]/g, "");
  s = s.replace(/\./g, "").replace(",", ".");
  const n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}

function uniqueSorted(values) {
  return [...new Set(values.filter((v) => v !== null && v !== undefined && v !== ""))].sort((a, b) =>
    String(a).localeCompare(String(b), "pt-BR")
  );
}

function fillSelect(select, options, placeholder) {
  const current = select.value;
  select.innerHTML = "";
  const optEl = document.createElement("option");
  optEl.value = "";
  optEl.textContent = placeholder;
  select.appendChild(optEl);
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt;
    el.textContent = opt;
    select.appendChild(el);
  }
  if (options.includes(current)) select.value = current;
}

/* ---------------- Navigation ---------------- */

function setupNavigation() {
  const links = document.querySelectorAll(".nav-link[data-page]");
  links.forEach((link) => {
    link.addEventListener("click", () => navigateTo(link.dataset.page));
  });

  document.getElementById("home-cta-btn").addEventListener("click", () => navigateTo("importar"));
}

function navigateTo(page) {
  document.querySelectorAll(".nav-link[data-page]").forEach((l) => {
    l.classList.toggle("active", l.dataset.page === page);
  });

  document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
  document.getElementById("page-" + page).classList.add("active");

  closeMobileNav();
  document.querySelector(".content").scrollTo?.({ top: 0 });
  window.scrollTo(0, 0);
}

function setupMobileNav() {
  const sidebar = document.getElementById("sidebar");
  const overlay = document.getElementById("sidebar-overlay");
  const toggle = document.getElementById("topbar-toggle");

  toggle.addEventListener("click", () => {
    sidebar.classList.toggle("open");
    overlay.classList.toggle("show");
  });

  overlay.addEventListener("click", closeMobileNav);
}

function closeMobileNav() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-overlay").classList.remove("show");
}

/* ---------------- Toasts & Loading ---------------- */

function showLoading(text) {
  const overlay = document.getElementById("loading-overlay");
  document.getElementById("loading-text").textContent = text || "Processando...";
  overlay.hidden = false;
}

function hideLoading() {
  document.getElementById("loading-overlay").hidden = true;
}

const TOAST_ICONS = { success: "✅", error: "⚠️", info: "ℹ️" };

function showToast(message, type = "info", duration = 4000) {
  const container = document.getElementById("toast-container");

  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<span class="toast-icon">${TOAST_ICONS[type] || TOAST_ICONS.info}</span><span>${message}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.classList.add("toast-hide");
    toast.addEventListener("animationend", () => toast.remove());
  }, duration);
}

/* ---------------- Extrato (xlsx) ---------------- */

async function handleExtratoFile(file, originador) {
  showLoading(`Importando extrato (${originador})...`);

  try {
    const buf = await file.arrayBuffer();
    const wb = XLSX.read(buf, { type: "array", cellDates: true });
    const ws = wb.Sheets[wb.SheetNames[0]];
    const json = XLSX.utils.sheet_to_json(ws, { defval: null });

    if (!json.length) {
      showToast("O arquivo de extrato está vazio.", "error");
      return;
    }

    const headers = Object.keys(json[0]);

    const dataCol = headers.find((h) => h.toLowerCase().includes("data") && h.toLowerCase().includes("mov"));
    const convCol = headers.find((h) => h.toLowerCase().includes("conv"));
    const valorCol = headers.find((h) => h.trim().toLowerCase() === "valor");
    const naturezaCol = headers.find((h) => h.toLowerCase().includes("natureza"));

    const rows = json.map((r) => {
      const data = parsearDataMovimento(r[dataCol]);

      const valido = data !== null;
      const valor = parseNumeroBR(r[valorCol]);
      const natureza = String(r[naturezaCol] || "").trim().toUpperCase();

      let mesAno = "";
      if (valido) {
        const mm = String(data.getMonth() + 1).padStart(2, "0");
        mesAno = `${mm}/${data.getFullYear()}`;
      }

      const raw = {};
      for (const h of headers) {
        const v = r[h];
        raw[h] = v instanceof Date ? v.toISOString() : v;
      }

      return {
        conv: r[convCol],
        data: valido ? data.toISOString() : null,
        mesAno,
        valorD: natureza === "D" ? valor : 0,
        valorC: natureza === "C" ? valor : 0,
        originador,
        raw,
      };
    });

    const rowsAntesTodos = getExtratoRows();
    if (rowsAntesTodos.length) {
      const snapC = rowsAntesTodos.reduce((s, r) => s + r.valorC, 0);
      const snapD = rowsAntesTodos.reduce((s, r) => s + r.valorD, 0);
      localStorage.setItem(STORAGE_EXTRATO_SNAPSHOT, JSON.stringify({ totalC: snapC, totalD: snapD, savedAt: new Date().toISOString() }));
    }

    extratosPorOriginador[originador] = { convCol, dataCol, headers, rows, importadoEm: new Date().toISOString() };
    localStorage.setItem(STORAGE_EXTRATOS, JSON.stringify(extratosPorOriginador));
    sbSave("extratos", extratosPorOriginador)
      .then(() => showToast("Extrato sincronizado com servidor ✓", "success", 3000))
      .catch((err) => { console.error(err); showToast("Erro ao sincronizar extrato: " + err.message, "error", 6000); });

    const filenameEl = document.getElementById(`extrato-filename-${slugOriginador(originador)}`);
    if (filenameEl) filenameEl.textContent = `✅ ${file.name}`;
    const labelEl = document.querySelector(`label[for="input-extrato-${slugOriginador(originador)}"]`);
    if (labelEl) labelEl.classList.add("loaded");

    renderAll();
    showToast(`Extrato (${originador}) importado: ${rows.length} lançamentos.`, "success");

    // Linha sem data não entra na conciliação (agrupada por mês/ano). O
    // descarte precisa ser visível — silenciá-lo já mascarou 427 lançamentos.
    const semData = rows.filter((r) => !r.mesAno);
    if (semData.length) {
      console.warn(`Extrato (${originador}): ${semData.length} linha(s) sem data de movimentação válida.`, {
        coluna: dataCol,
        exemplos: semData.slice(0, 5).map((r) => r.raw[dataCol]),
      });
      showToast(
        `${semData.length} lançamento(s) sem data válida em "${dataCol}" ficaram fora da conciliação. Detalhes no console (F12).`,
        "error",
        9000
      );
    }
  } catch (err) {
    console.error("Erro ao importar extrato:", err);
    showToast("Erro ao importar o extrato. Verifique o arquivo.", "error");
  } finally {
    hideLoading();
  }
}

/* ---------------- Retorno (csv) ---------------- */

async function handleRetornoFile(file) {
  showLoading("Importando arquivo retorno...");

  try {
    const buf = await file.arrayBuffer();
    const text = new TextDecoder("windows-1252").decode(buf);

    const parsed = Papa.parse(text, {
      header: true,
      delimiter: ";",
      skipEmptyLines: true,
    });

    if (!parsed.data.length) {
      showToast("O arquivo de retorno está vazio.", "error");
      return;
    }

    const rows = parsed.data.map((r) => ({
      convenio: r["convenio"],
      competencia: r["competencia"],
      valor: parseNumeroBR(r["total_valor_descontado"]),
    }));

    retornoData = { rows, importadoEm: new Date().toISOString() };
    localStorage.setItem(STORAGE_RETORNO, JSON.stringify(retornoData));
    sbSave("retorno", retornoData)
      .then(() => showToast("Retorno sincronizado com servidor ✓", "success", 3000))
      .catch((err) => { console.error(err); showToast("Erro ao sincronizar retorno: " + err.message, "error", 6000); });

    document.getElementById("retorno-filename").textContent = `✅ ${file.name}`;
    document.querySelector('label[for="input-retorno"]').classList.add("loaded");

    renderAll();
    showToast(`Arquivo retorno importado: ${rows.length} registros.`, "success");
  } catch (err) {
    console.error("Erro ao importar retorno:", err);
    showToast("Erro ao importar o arquivo de retorno. Verifique o arquivo.", "error");
  } finally {
    hideLoading();
  }
}

/* ---------------- Render: Home ---------------- */

function renderHome() {
  const selectOriginador = document.getElementById("home-originador");
  fillSelect(selectOriginador, ORIGINADORES.filter((o) => extratosPorOriginador[o]), "Todos Originadores");
  const filtroOriginador = selectOriginador.value;

  const rows = getExtratoRows(filtroOriginador);
  const totalC = rows.reduce((acc, r) => acc + r.valorC, 0);
  const totalD = rows.reduce((acc, r) => acc + r.valorD, 0);
  const conv = uniqueSorted(rows.map((r) => r.conv)).length;

  document.getElementById("home-total-c").textContent = moeda(totalC);
  document.getElementById("home-total-d").textContent = moeda(totalD);
  document.getElementById("home-saldo").textContent = moeda(totalC - totalD);

  try {
    const snap = JSON.parse(localStorage.getItem(STORAGE_EXTRATO_SNAPSHOT));
    const setDelta = (id, atual, anterior) => {
      const el = document.getElementById(id);
      if (!el) return;
      if (!snap || anterior == null) { el.hidden = true; return; }
      const diff = atual - anterior;
      const pct = anterior !== 0 ? ((diff / Math.abs(anterior)) * 100).toFixed(1) : null;
      const sinal = diff >= 0 ? "▲" : "▼";
      const abrev = (() => {
        const abs = Math.abs(diff);
        if (abs >= 1e6) return `R$ ${(abs / 1e6).toFixed(1)}M`;
        if (abs >= 1e3) return `R$ ${(abs / 1e3).toFixed(0)}K`;
        return moeda(diff);
      })();
      el.textContent = `${sinal} ${abrev}${pct != null ? ` (${pct}%)` : ""} vs importação anterior`;
      el.className = `kpi-delta ${diff >= 0 ? "kpi-delta-up" : "kpi-delta-down"}`;
      el.hidden = false;
    };
    setDelta("home-delta-c", totalC, snap?.totalC);
    setDelta("home-delta-d", totalD, snap?.totalD);
    setDelta("home-delta-saldo", totalC - totalD, snap ? snap.totalC - snap.totalD : null);
  } catch { /* silencioso */ }
  document.getElementById("home-conv").textContent = conv;

  const dados = computeConciliacao();
  const cruzados = dados.filter((r) => r.status in STATUS_DONUT_COLORS);
  const conciliados = cruzados.filter((r) => r.status !== "divergente").length;
  const taxa = cruzados.length ? Math.round((conciliados / cruzados.length) * 100) : 0;
  document.getElementById("home-taxa-conciliacao").textContent = `${taxa}%`;

  const pendencias = dados.filter((r) => r.status !== "ok" && r.status !== "conciliado-maior" && r.status !== "conciliado-menor").length;
  document.getElementById("home-pendencias").textContent = pendencias;

  const hasData = hasAnyExtrato() || !!(retornoData && retornoData.rows.length);
  document.getElementById("home-cta").hidden = hasData;

  const ultimaExtrato = latestExtratoImportadoEm();
  const datas = [ultimaExtrato, retornoData?.importadoEm ? new Date(retornoData.importadoEm) : null].filter(Boolean);
  const meta = document.getElementById("home-last-update");
  if (datas.length) {
    const ultima = new Date(Math.max(...datas.map((d) => d.getTime())));
    meta.textContent = `Atualizado em ${ultima.toLocaleDateString("pt-BR")} às ${ultima.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
  } else {
    meta.textContent = "";
  }

  const hora = new Date().getHours();
  const saudacao = hora < 12 ? "Bom dia" : hora < 18 ? "Boa tarde" : "Boa noite";
  const session = getSession();
  document.getElementById("home-greeting").textContent = `${saudacao}${session ? ", " + session.label : ""} · ${new Date().toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" })}`;


  renderChartCreditoDebito(totalC, totalD, rows);
  renderChartTopConvenios(rows);
  renderChartHomeConciliacaoStatus(dados);
  renderHomeDivergencias(dados);
}

function renderHomeDivergencias(dados) {
  const tbody = document.getElementById("home-divergencias-tbody");
  const empty = document.getElementById("home-divergencias-empty");
  const table = document.getElementById("home-divergencias-table");

  tbody.innerHTML = "";

  const top = dados
    .filter((r) => r.status === "divergente")
    .sort((a, b) => Math.abs(b.diferenca) - Math.abs(a.diferenca))
    .slice(0, 5);

  if (!top.length) {
    table.hidden = true;
    empty.hidden = false;
    return;
  }

  table.hidden = false;
  empty.hidden = true;

  for (const r of top) {
    const status = STATUS_LABEL[r.status];
    const diffClass = r.diferenca < 0 ? "text-danger" : "text-success";
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.convenio}<br><span style="font-size:11px;color:var(--text-faint)">${r.mesAno}</span></td>
      <td class="${diffClass}" style="font-weight:700">${moeda(r.diferenca)}</td>
      <td><span class="badge ${status.className}">${status.label}</span></td>
    `;
    tbody.appendChild(tr);
  }
}

/* ---------------- Charts: Home ---------------- */

function renderChartCreditoDebito(totalC, totalD, rows) {
  const canvas = document.getElementById("chart-credito-debito");
  const empty = document.getElementById("chart-credito-debito-empty");

  if (chartCreditoDebito) {
    chartCreditoDebito.destroy();
    chartCreditoDebito = null;
  }

  if (typeof Chart === "undefined") {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Biblioteca de gráficos indisponível.";
    return;
  }

  if (!rows.length || (totalC === 0 && totalD === 0)) {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Sem dados do extrato.";
    return;
  }

  canvas.style.display = "";
  empty.hidden = true;

  const saldo = totalC - totalD;
  const saldoAbrev = (() => {
    const abs = Math.abs(saldo);
    const sign = saldo < 0 ? "-" : "";
    if (abs >= 1e6) return `${sign}R$ ${(abs / 1e6).toFixed(1)}M`;
    if (abs >= 1e3) return `${sign}R$ ${(abs / 1e3).toFixed(0)}K`;
    return moeda(saldo);
  })();

  chartCreditoDebito = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: ["Crédito", "Débito"],
      datasets: [
        {
          data: [totalC, totalD],
          backgroundColor: [CHART_COLORS.success, CHART_COLORS.danger],
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        centerText: { value: saldoAbrev, label: "Saldo", color: saldo >= 0 ? CHART_COLORS.success : CHART_COLORS.danger },
        legend: { position: "bottom" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${moeda(ctx.raw)}` } },
      },
    },
  });
}

function renderChartTopConvenios(rows) {
  const canvas = document.getElementById("chart-top-convenios");
  const empty = document.getElementById("chart-top-convenios-empty");

  if (chartTopConvenios) {
    chartTopConvenios.destroy();
    chartTopConvenios = null;
  }

  if (typeof Chart === "undefined") {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Biblioteca de gráficos indisponível.";
    return;
  }

  if (!rows.length) {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Sem dados do extrato.";
    return;
  }

  const grupos = new Map();
  for (const r of rows) {
    const chave = r.conv ?? "—";
    grupos.set(chave, (grupos.get(chave) || 0) + r.valorC - r.valorD);
  }

  const top = [...grupos.entries()].sort((a, b) => b[1] - a[1]).slice(0, 5);

  canvas.style.display = "";
  empty.hidden = true;

  chartTopConvenios = new Chart(canvas, {
    type: "bar",
    data: {
      labels: top.map(([conv]) => conv),
      datasets: [
        {
          label: "Saldo",
          data: top.map(([, v]) => v),
          backgroundColor: CHART_COLORS.accent,
          borderRadius: 6,
          maxBarThickness: 32,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => moeda(ctx.raw) } },
      },
      scales: {
        x: { ticks: { callback: (v) => moedaCompacta(v) }, grid: { display: false } },
        y: { grid: { display: false } },
      },
    },
  });
}

/* ---------------- Render: Ranking ---------------- */

function renderRanking() {
  const tbody = document.getElementById("ranking-tbody");
  const empty = document.getElementById("ranking-empty");
  const select = document.getElementById("ranking-convenio");
  const selectOriginador = document.getElementById("ranking-originador");

  tbody.innerHTML = "";

  fillSelect(selectOriginador, ORIGINADORES.filter((o) => extratosPorOriginador[o]), "Todos Originadores");
  const filtroOriginador = selectOriginador.value;
  const rows = getExtratoRows(filtroOriginador);

  if (!rows.length) {
    empty.hidden = false;
    fillSelect(select, [], "Todos Convênios");
    return;
  }

  empty.hidden = true;

  fillSelect(select, uniqueSorted(rows.map((r) => r.conv)), "Todos Convênios");

  const filtro = select.value;
  const filtered = filtro ? rows.filter((r) => r.conv === filtro) : rows;

  const grupos = new Map();
  for (const r of filtered) {
    const chave = r.conv ?? "—";
    if (!grupos.has(chave)) grupos.set(chave, { valorD: 0, valorC: 0 });
    const g = grupos.get(chave);
    g.valorD += r.valorD;
    g.valorC += r.valorC;
  }

  const ordenados = [...grupos.entries()].sort((a, b) => String(a[0]).localeCompare(String(b[0]), "pt-BR"));

  let totalD = 0;
  let totalC = 0;

  for (const [conv, g] of ordenados) {
    const saldo = g.valorC - g.valorD;
    totalD += g.valorD;
    totalC += g.valorC;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${conv}</td>
      <td>${moeda(g.valorD)}</td>
      <td>${moeda(g.valorC)}</td>
      <td>${moeda(saldo)}</td>
    `;
    tbody.appendChild(tr);
  }

  if (ordenados.length > 1) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `
      <td>TOTAL</td>
      <td>${moeda(totalD)}</td>
      <td>${moeda(totalC)}</td>
      <td>${moeda(totalC - totalD)}</td>
    `;
    tbody.appendChild(tr);
  }
}

/* ---------------- Render: Evolução ---------------- */

function renderEvolucao() {
  const tbody = document.getElementById("evolucao-tbody");
  const empty = document.getElementById("evolucao-empty");
  const selectConv = document.getElementById("evolucao-convenio");
  const selectOriginador = document.getElementById("evolucao-originador");
  const inputInicio = document.getElementById("evolucao-inicio");
  const inputFim = document.getElementById("evolucao-fim");

  tbody.innerHTML = "";

  fillSelect(selectOriginador, ORIGINADORES.filter((o) => extratosPorOriginador[o]), "Todos Originadores");
  const filtroOriginador = selectOriginador.value;
  const rows = getExtratoRows(filtroOriginador);

  if (!rows.length) {
    empty.hidden = false;
    fillSelect(selectConv, [], "Todos Convênios");
    renderChartEvolucao([]);
    return;
  }

  empty.hidden = true;

  fillSelect(selectConv, uniqueSorted(rows.map((r) => r.conv)), "Todos Convênios");

  const filtroConv = selectConv.value;
  const inicio = inputInicio.value ? new Date(inputInicio.value + "T00:00:00") : null;
  const fim = inputFim.value ? new Date(inputFim.value + "T23:59:59") : null;

  const filtered = rows.filter((r) => {
    if (filtroConv && r.conv !== filtroConv) return false;
    if (r.data) {
      const d = new Date(r.data);
      if (inicio && d < inicio) return false;
      if (fim && d > fim) return false;
    } else if (inicio || fim) {
      return false;
    }
    return true;
  });

  renderChartEvolucao(filtered);

  const grupos = new Map();
  for (const r of filtered) {
    const chave = `${r.conv ?? "—"}__${r.mesAno}`;
    if (!grupos.has(chave)) grupos.set(chave, { conv: r.conv ?? "—", mesAno: r.mesAno, valorD: 0, valorC: 0 });
    const g = grupos.get(chave);
    g.valorD += r.valorD;
    g.valorC += r.valorC;
  }

  const ordenados = [...grupos.values()].sort((a, b) => {
    const convCompare = String(a.conv).localeCompare(String(b.conv), "pt-BR");
    if (convCompare !== 0) return convCompare;
    return mesAnoParaOrdenacao(a.mesAno) - mesAnoParaOrdenacao(b.mesAno);
  });

  for (const g of ordenados) {
    const saldo = g.valorC - g.valorD;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${g.conv}</td>
      <td>${g.mesAno || "—"}</td>
      <td>${moeda(g.valorD)}</td>
      <td>${moeda(g.valorC)}</td>
      <td>${moeda(saldo)}</td>
    `;
    tbody.appendChild(tr);
  }
}

function mesAnoParaOrdenacao(mesAno) {
  if (!mesAno) return 0;
  const [mes, ano] = mesAno.split("/");
  return Number(ano) * 100 + Number(mes);
}

/* ---------------- Chart: Evolução ---------------- */

function renderChartEvolucao(filtered) {
  const canvas = document.getElementById("chart-evolucao");
  const empty = document.getElementById("chart-evolucao-empty");

  if (chartEvolucao) {
    chartEvolucao.destroy();
    chartEvolucao = null;
  }

  if (typeof Chart === "undefined") {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Biblioteca de gráficos indisponível.";
    return;
  }

  const grupos = new Map();
  for (const r of filtered) {
    if (!r.mesAno) continue;
    if (!grupos.has(r.mesAno)) grupos.set(r.mesAno, { valorD: 0, valorC: 0 });
    const g = grupos.get(r.mesAno);
    g.valorD += r.valorD;
    g.valorC += r.valorC;
  }

  const meses = [...grupos.keys()].sort((a, b) => mesAnoParaOrdenacao(a) - mesAnoParaOrdenacao(b));

  if (!meses.length) {
    canvas.style.display = "none";
    empty.hidden = false;
    return;
  }

  canvas.style.display = "";
  empty.hidden = true;

  const dataC = meses.map((m) => grupos.get(m).valorC);
  const dataD = meses.map((m) => grupos.get(m).valorD);
  const dataSaldo = meses.map((m) => grupos.get(m).valorC - grupos.get(m).valorD);

  chartEvolucao = new Chart(canvas, {
    data: {
      labels: meses,
      datasets: [
        { type: "bar", label: "Crédito", data: dataC, backgroundColor: CHART_COLORS.success, borderRadius: 4, maxBarThickness: 36 },
        { type: "bar", label: "Débito", data: dataD, backgroundColor: CHART_COLORS.danger, borderRadius: 4, maxBarThickness: 36 },
        { type: "line", label: "Saldo", data: dataSaldo, borderColor: CHART_COLORS.accentLight, backgroundColor: CHART_COLORS.accentLight, tension: 0.3, fill: false },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${moeda(ctx.raw)}` } },
      },
      scales: {
        x: { grid: { display: false } },
        y: { ticks: { callback: (v) => moedaCompacta(v) }, grid: { display: false } },
      },
    },
  });
}

/* ---------------- Render: Retorno ---------------- */

function renderRetorno() {
  const tbody = document.getElementById("retorno-tbody");
  const empty = document.getElementById("retorno-empty");
  const selectConv = document.getElementById("retorno-convenio");
  const selectComp = document.getElementById("retorno-competencia");

  tbody.innerHTML = "";

  if (!retornoData || !retornoData.rows.length) {
    empty.hidden = false;
    fillSelect(selectConv, [], "Todos Convênios");
    fillSelect(selectComp, [], "Todas Competências");
    return;
  }

  empty.hidden = true;

  const { rows } = retornoData;
  fillSelect(selectConv, uniqueSorted(rows.map((r) => r.convenio)), "Todos Convênios");
  fillSelect(selectComp, uniqueSorted(rows.map((r) => r.competencia)), "Todas Competências");

  const filtroConv = selectConv.value;
  const filtroComp = selectComp.value;

  const filtered = rows.filter((r) => {
    if (filtroConv && r.convenio !== filtroConv) return false;
    if (filtroComp && r.competencia !== filtroComp) return false;
    return true;
  });

  let total = 0;

  for (const r of filtered) {
    total += r.valor;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.convenio ?? "—"}</td>
      <td>${r.competencia ?? "—"}</td>
      <td>${moeda(r.valor)}</td>
    `;
    tbody.appendChild(tr);
  }

  if (filtered.length > 1) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `
      <td colspan="2">TOTAL</td>
      <td>${moeda(total)}</td>
    `;
    tbody.appendChild(tr);
  }
}

/* ---------------- Conciliação ---------------- */

function computeConciliacao() {
  const map = new Map();
  const extratoData = extratosPorOriginador[ORIGINADOR_RETORNO];

  if (extratoData) {
    for (const r of extratoData.rows) {
      if (!r.mesAno) continue;

      const key = `${normalizeNome(r.conv)}__${r.mesAno}`;
      if (!map.has(key)) {
        map.set(key, {
          convExtrato: r.conv,
          convRetorno: null,
          mesAno: r.mesAno,
          valorExtrato: 0,
          valorDebitoExtrato: 0,
          qtdLancamentosExtrato: 0,
          valorRetorno: 0,
        });
      }
      const item = map.get(key);
      item.valorExtrato += r.valorC;
      item.valorDebitoExtrato += r.valorD;
      item.qtdLancamentosExtrato += 1;
    }
  }

  if (retornoData) {
    for (const r of retornoData.rows) {
      const mesAnoComp = competenciaParaMesAno(r.competencia);
      if (!mesAnoComp) continue;
      const mesAno = mesAnoProximoMes(mesAnoComp);
      if (!mesAno) continue;

      const key = `${normalizeNome(r.convenio)}__${mesAno}`;
      if (!map.has(key)) {
        map.set(key, {
          convExtrato: null,
          convRetorno: r.convenio,
          mesAno,
          valorExtrato: 0,
          valorDebitoExtrato: 0,
          qtdLancamentosExtrato: 0,
          valorRetorno: 0,
        });
      }

      const item = map.get(key);
      if (!item.convRetorno) item.convRetorno = r.convenio;
      item.valorRetorno += r.valor;
    }
  }

  const resultado = [...map.values()].map((item) => {
    const convenio = item.convExtrato ?? item.convRetorno ?? "—";
    const diferenca = item.valorExtrato - item.valorRetorno;

    let status;
    if (item.convExtrato === null) status = "sem-extrato";
    else if (item.convRetorno === null) status = "sem-retorno";
    else if (Math.abs(diferenca) < TOLERANCIA) status = "ok";
    else if (item.valorRetorno !== 0 && Math.abs(diferenca) <= Math.abs(item.valorRetorno) * 0.05) {
      status = diferenca > 0 ? "conciliado-maior" : "conciliado-menor";
    } else status = "divergente";

    return {
      convenio,
      mesAno: item.mesAno,
      valorExtrato: item.valorExtrato,
      valorDebitoExtrato: item.valorDebitoExtrato,
      qtdLancamentosExtrato: item.qtdLancamentosExtrato,
      valorRetorno: item.valorRetorno,
      diferenca,
      status,
    };
  });

  resultado.sort((a, b) => {
    const convCompare = String(a.convenio).localeCompare(String(b.convenio), "pt-BR");
    if (convCompare !== 0) return convCompare;
    return mesAnoParaOrdenacao(a.mesAno) - mesAnoParaOrdenacao(b.mesAno);
  });

  return resultado;
}

const STATUS_LABEL = {
  ok: { label: "Conciliado", className: "badge-success" },
  "conciliado-maior": { label: "Conciliado a maior", className: "badge-success" },
  "conciliado-menor": { label: "Conciliado a menor", className: "badge-success" },
  divergente: { label: "Divergente", className: "badge-danger" },
  "sem-extrato": { label: "Sem Extrato", className: "badge-warning" },
  "sem-retorno": { label: "Sem Retorno", className: "badge-warning" },
};

function filtrarConciliacao(dados) {
  const filtroConv = document.getElementById("conciliacao-convenio").value;
  const filtroMes = document.getElementById("conciliacao-mes").value;
  const filtroStatus = document.getElementById("conciliacao-status").value;

  return dados.filter((r) => {
    if (filtroConv && r.convenio !== filtroConv) return false;
    if (filtroMes && r.mesAno !== filtroMes) return false;
    if (filtroStatus && r.status !== filtroStatus) return false;
    return true;
  });
}

function updateConciliacaoSummary(dados) {
  const contagem = { ok: 0, "conciliado-maior": 0, "conciliado-menor": 0, divergente: 0, "sem-extrato": 0, "sem-retorno": 0 };
  for (const r of dados) contagem[r.status]++;

  document.getElementById("conc-summary-ok").textContent = contagem.ok + contagem["conciliado-maior"] + contagem["conciliado-menor"];
  document.getElementById("conc-summary-divergente").textContent = contagem.divergente;
  document.getElementById("conc-summary-sem-retorno").textContent = contagem["sem-retorno"];
  document.getElementById("conc-summary-sem-extrato").textContent = contagem["sem-extrato"];
}

const STATUS_DONUT_COLORS = {
  ok: CHART_COLORS.success,
  "conciliado-maior": CHART_COLORS.successLight,
  "conciliado-menor": CHART_COLORS.teal,
  divergente: CHART_COLORS.danger,
};

function renderStatusDonut(canvasId, emptyId, dados, existingChart) {
  const canvas = document.getElementById(canvasId);
  const empty = document.getElementById(emptyId);

  if (existingChart) existingChart.destroy();

  if (typeof Chart === "undefined") {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Biblioteca de gráficos indisponível.";
    return null;
  }

  const cruzados = dados.filter((r) => r.status in STATUS_DONUT_COLORS);

  if (!cruzados.length) {
    canvas.style.display = "none";
    empty.hidden = false;
    return null;
  }

  const contagem = new Map();
  for (const r of cruzados) contagem.set(r.status, (contagem.get(r.status) || 0) + 1);

  const statusOrdenados = Object.keys(STATUS_DONUT_COLORS).filter((s) => contagem.has(s));
  const total = cruzados.length;
  const conciliados = cruzados.filter((r) => r.status !== "divergente").length;
  const taxa = Math.round((conciliados / total) * 100);

  canvas.style.display = "";
  empty.hidden = true;

  return new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: statusOrdenados.map((s) => STATUS_LABEL[s].label),
      datasets: [
        {
          data: statusOrdenados.map((s) => contagem.get(s)),
          backgroundColor: statusOrdenados.map((s) => STATUS_DONUT_COLORS[s]),
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      plugins: {
        centerText: { value: `${taxa}%`, label: "Conciliado", color: taxa >= 70 ? CHART_COLORS.success : taxa >= 40 ? CHART_COLORS.warning : CHART_COLORS.danger },
        legend: { position: "bottom", labels: { boxWidth: 12, padding: 14 } },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.label}: ${ctx.raw} (${((ctx.raw / total) * 100).toFixed(0)}%)`,
          },
        },
      },
    },
  });
}

function renderChartConciliacaoStatus(dados) {
  chartConciliacaoStatus = renderStatusDonut("chart-conciliacao-status", "chart-conciliacao-status-empty", dados, chartConciliacaoStatus);
}

function renderChartHomeConciliacaoStatus(dados) {
  chartHomeConciliacaoStatus = renderStatusDonut("chart-home-conciliacao-status", "chart-home-conciliacao-status-empty", dados, chartHomeConciliacaoStatus);
}

function renderChartConciliacaoComparativo(filtered) {
  const canvas = document.getElementById("chart-conciliacao-comparativo");
  const empty = document.getElementById("chart-conciliacao-comparativo-empty");

  if (chartConciliacaoComparativo) {
    chartConciliacaoComparativo.destroy();
    chartConciliacaoComparativo = null;
  }

  if (typeof Chart === "undefined") {
    canvas.style.display = "none";
    empty.hidden = false;
    empty.textContent = "Biblioteca de gráficos indisponível.";
    return;
  }

  const cruzados = filtered.filter((r) => r.valorRetorno !== 0 && r.valorExtrato !== 0);

  if (!cruzados.length) {
    canvas.style.display = "none";
    empty.hidden = false;
    return;
  }

  const LIMITE = 8;
  const top = [...cruzados]
    .sort((a, b) => (b.valorRetorno + b.valorExtrato) - (a.valorRetorno + a.valorExtrato))
    .slice(0, LIMITE)
    .reverse();

  canvas.style.display = "";
  empty.hidden = true;

  chartConciliacaoComparativo = new Chart(canvas, {
    type: "bar",
    data: {
      labels: top.map((r) => `${r.convenio} - ${r.mesAno}`),
      datasets: [
        { label: "Retorno", data: top.map((r) => r.valorRetorno), backgroundColor: CHART_COLORS.accent, borderRadius: 4, maxBarThickness: 16 },
        { label: "Extrato", data: top.map((r) => r.valorExtrato), backgroundColor: CHART_COLORS.success, borderRadius: 4, maxBarThickness: 16 },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 12, padding: 14 } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${moeda(ctx.raw)}` } },
      },
      scales: {
        x: { ticks: { callback: (v) => moedaCompacta(v) }, grid: { display: false } },
        y: { grid: { display: false }, ticks: { autoSkip: false } },
      },
    },
  });
}

function renderConciliacao() {
  const tbody = document.getElementById("conciliacao-tbody");
  const empty = document.getElementById("conciliacao-empty");
  const selectConv = document.getElementById("conciliacao-convenio");
  const selectMes = document.getElementById("conciliacao-mes");
  const selectStatus = document.getElementById("conciliacao-status");

  tbody.innerHTML = "";

  const dados = computeConciliacao();

  if (!dados.length) {
    empty.hidden = false;
    fillSelect(selectConv, [], "Todos Convênios");
    fillSelect(selectMes, [], "Todos os Meses");
    updateConciliacaoSummary([]);
    renderChartConciliacaoStatus([]);
    renderChartConciliacaoComparativo([]);
    return;
  }

  empty.hidden = true;

  fillSelect(selectConv, uniqueSorted(dados.map((r) => r.convenio)), "Todos Convênios");
  fillSelect(
    selectMes,
    [...new Set(dados.map((r) => r.mesAno))].sort((a, b) => mesAnoParaOrdenacao(a) - mesAnoParaOrdenacao(b)),
    "Todos os Meses"
  );

  const filtered = filtrarConciliacao(dados);

  const clearBtn = document.getElementById("conciliacao-clear-filters");
  if (clearBtn) {
    const hasFilter = ["conciliacao-convenio", "conciliacao-mes", "conciliacao-status"].some(
      (id) => document.getElementById(id).value !== ""
    );
    clearBtn.hidden = !hasFilter;
  }

  let totalExtrato = 0;
  let totalRetorno = 0;

  for (const r of filtered) {
    totalExtrato += r.valorExtrato;
    totalRetorno += r.valorRetorno;

    const status = STATUS_LABEL[r.status];
    const diffClass = Math.abs(r.diferenca) < TOLERANCIA ? "text-dim" : r.diferenca < 0 ? "text-danger" : "text-success";

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${r.convenio}</td>
      <td>${r.mesAno}</td>
      <td>${moeda(r.valorRetorno)}</td>
      <td>${moeda(r.valorExtrato)}</td>
      <td class="${diffClass}">${moeda(r.diferenca)}</td>
      <td><span class="badge ${status.className}">${status.label}</span></td>
    `;
    tbody.appendChild(tr);
  }

  if (filtered.length > 1) {
    const tr = document.createElement("tr");
    tr.className = "total-row";
    tr.innerHTML = `
      <td colspan="2">TOTAL</td>
      <td>${moeda(totalRetorno)}</td>
      <td>${moeda(totalExtrato)}</td>
      <td>${moeda(totalExtrato - totalRetorno)}</td>
      <td></td>
    `;
    tbody.appendChild(tr);
  }

  updateConciliacaoSummary(filtered);
  renderChartConciliacaoStatus(filtered);
  renderChartConciliacaoComparativo(filtered);
}

/* ---------------- Render: All ---------------- */

function safeRender(fn) {
  try {
    fn();
  } catch (err) {
    console.error(`Erro ao renderizar ${fn.name}:`, err);
  }
}

function renderAll() {
  safeRender(renderHome);
  safeRender(renderRanking);
  safeRender(renderEvolucao);
  safeRender(renderRetorno);
  safeRender(renderConciliacao);
  safeRender(updateStatusPills);
}

/* ---------------- Setup ---------------- */

function setupUploads() {
  const grid = document.getElementById("extrato-originador-grid");
  grid.innerHTML = ORIGINADORES.map((o) => {
    const slug = slugOriginador(o);
    return `
      <div class="extrato-originador-card">
        <h3>${o}</h3>
        <label class="dropzone" for="input-extrato-${slug}">
          <span class="dropzone-icon"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></span>
          <span id="extrato-filename-${slug}">Clique ou arraste o .xlsx aqui</span>
        </label>
        <input type="file" id="input-extrato-${slug}" accept=".xlsx,.xls" hidden>
      </div>
    `;
  }).join("");

  ORIGINADORES.forEach((o) => {
    const slug = slugOriginador(o);
    const input = document.getElementById(`input-extrato-${slug}`);
    input.addEventListener("change", (e) => {
      if (e.target.files[0]) handleExtratoFile(e.target.files[0], o);
    });
    setupDropzone(`input-extrato-${slug}`, (file) => handleExtratoFile(file, o));
  });

  const inputRetorno = document.getElementById("input-retorno");
  inputRetorno.addEventListener("change", (e) => {
    if (e.target.files[0]) handleRetornoFile(e.target.files[0]);
  });
  setupDropzone("input-retorno", handleRetornoFile);
}

function setupDropzone(inputId, handler) {
  const input = document.getElementById(inputId);
  const label = document.querySelector(`label[for="${inputId}"]`);

  ["dragenter", "dragover"].forEach((evt) =>
    label.addEventListener(evt, (e) => {
      e.preventDefault();
      label.classList.add("dragover");
    })
  );

  ["dragleave", "drop"].forEach((evt) =>
    label.addEventListener(evt, (e) => {
      e.preventDefault();
      label.classList.remove("dragover");
    })
  );

  label.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) {
      input.files = e.dataTransfer.files;
      handler(file);
    }
  });
}

function setupFilters() {
  document.getElementById("ranking-convenio").addEventListener("change", renderRanking);
  document.getElementById("ranking-originador").addEventListener("change", renderRanking);
  document.getElementById("evolucao-convenio").addEventListener("change", renderEvolucao);
  document.getElementById("evolucao-originador").addEventListener("change", renderEvolucao);
  document.getElementById("evolucao-inicio").addEventListener("change", renderEvolucao);
  document.getElementById("evolucao-fim").addEventListener("change", renderEvolucao);
  document.getElementById("retorno-convenio").addEventListener("change", renderRetorno);
  document.getElementById("retorno-competencia").addEventListener("change", renderRetorno);
  document.getElementById("home-originador").addEventListener("change", renderHome);
  const concFilters = ["conciliacao-convenio", "conciliacao-mes", "conciliacao-status"];
  const clearBtn = document.getElementById("conciliacao-clear-filters");

  function onConciliacaoFilter() {
    const hasFilter = concFilters.some((id) => document.getElementById(id).value !== "");
    clearBtn.hidden = !hasFilter;
    renderConciliacao();
  }

  concFilters.forEach((id) => document.getElementById(id).addEventListener("change", onConciliacaoFilter));

  clearBtn.addEventListener("click", () => {
    concFilters.forEach((id) => (document.getElementById(id).value = ""));
    clearBtn.hidden = true;
    renderConciliacao();
  });
}

function updateStatusPills() {
  const pillExtrato = document.getElementById("status-extrato");
  const pillRetorno = document.getElementById("status-retorno");
  const statusGrid = document.getElementById("import-status-grid");

  const totalLanc = countExtratoLancamentos();
  const qtdOriginadores = ORIGINADORES.filter((o) => extratosPorOriginador[o]).length;

  if (totalLanc > 0) {
    pillExtrato.classList.add("ok");
    pillExtrato.lastChild.textContent = ` Extrato: ${totalLanc} lançamentos (${qtdOriginadores}/${ORIGINADORES.length} originadores)`;
  } else {
    pillExtrato.classList.remove("ok");
    pillExtrato.lastChild.textContent = " Nenhum extrato carregado";
  }

  if (retornoData && retornoData.rows.length) {
    pillRetorno.classList.add("ok");
    pillRetorno.lastChild.textContent = ` Retorno: ${retornoData.rows.length} registros`;
  } else {
    pillRetorno.classList.remove("ok");
    pillRetorno.lastChild.textContent = " Retorno não carregado";
  }

  if (statusGrid) {
    const cardsExtrato = ORIGINADORES.map((o) => {
      const d = extratosPorOriginador[o];
      const ok = d && d.rows.length;
      return `
        <div class="status-card ${ok ? "ok" : ""}">
          <span class="status-dot"></span>
          <div>
            <strong>${o}</strong>
            <p>${ok ? `${d.rows.length} lançamentos importados` : "Nenhum arquivo importado"}</p>
          </div>
        </div>
      `;
    }).join("");

    const okRetorno = retornoData && retornoData.rows.length;
    const cardRetorno = `
      <div class="status-card ${okRetorno ? "ok" : ""}">
        <span class="status-dot"></span>
        <div>
          <strong>Arquivo Retorno (VemCard)</strong>
          <p>${okRetorno ? `${retornoData.rows.length} registros importados` : "Nenhum arquivo importado"}</p>
        </div>
      </div>
    `;

    statusGrid.innerHTML = cardsExtrato + cardRetorno;
  }
}

/* ---------------- Export ---------------- */

function exportTableToExcel(tableId, filename) {
  const table = document.getElementById(tableId);
  const wb = XLSX.utils.table_to_book(table, { sheet: "Dados" });
  XLSX.writeFile(wb, filename);
  showToast(`Arquivo "${filename}" exportado.`, "success");
}

function exportConciliacaoExcel() {
  const filtered = filtrarConciliacao(computeConciliacao());

  if (!filtered.length) {
    showToast("Nenhum dado de conciliação para exportar.", "error");
    return;
  }

  const linhasConciliacao = filtered.map((r) => ({
    "Convênio": r.convenio,
    "Mês": r.mesAno,
    "Valor Retorno": r.valorRetorno,
    "Valor Extrato (Crédito)": r.valorExtrato,
    "Valor Extrato (Débito)": r.valorDebitoExtrato,
    "Saldo Extrato": r.valorExtrato - r.valorDebitoExtrato,
    "Qtd Lançamentos Extrato": r.qtdLancamentosExtrato,
    "Diferença": r.diferenca,
    "Status": STATUS_LABEL[r.status].label,
  }));

  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(linhasConciliacao), "Conciliação");

  const extratoData = extratosPorOriginador[ORIGINADOR_RETORNO];
  if (extratoData && extratoData.rows.length) {
    const { dataCol } = extratoData;

    const chaves = new Set(filtered.map((r) => `${normalizeNome(r.convenio)}__${r.mesAno}`));
    const linhasOrigem = extratoData.rows.filter((r) => chaves.has(`${normalizeNome(r.conv)}__${r.mesAno}`));

    const linhasExtrato = linhasOrigem.map((r) => {
      const linha = { ...r.raw, "Mês/Ano": r.mesAno };
      if (dataCol && linha[dataCol]) linha[dataCol] = new Date(linha[dataCol]);
      return linha;
    });

    if (linhasExtrato.length) {
      const wsExtrato = XLSX.utils.json_to_sheet(linhasExtrato, { dateNF: "dd/mm/yyyy" });
      XLSX.utils.book_append_sheet(wb, wsExtrato, "Extrato");
    }
  }

  XLSX.writeFile(wb, "conciliacao.xlsx");
  showToast('Arquivo "conciliacao.xlsx" exportado.', "success");
}

function setupExports() {
  document.getElementById("ranking-export").addEventListener("click", () =>
    exportTableToExcel("ranking-table", "ranking_extrato.xlsx")
  );
  document.getElementById("evolucao-export").addEventListener("click", () =>
    exportTableToExcel("evolucao-table", "evolucao_extrato.xlsx")
  );
  document.getElementById("retorno-export").addEventListener("click", () =>
    exportTableToExcel("retorno-table", "arquivo_retorno.xlsx")
  );
  document.getElementById("conciliacao-export").addEventListener("click", exportConciliacaoExcel);
}

function setupClear() {
  document.getElementById("btn-limpar").addEventListener("click", () => {
    if (!confirm("Remover todos os dados importados?")) return;

    localStorage.removeItem(STORAGE_EXTRATO);
    localStorage.removeItem(STORAGE_EXTRATOS);
    localStorage.removeItem(STORAGE_RETORNO);
    localStorage.removeItem(STORAGE_EXTRATO_SNAPSHOT);
    ORIGINADORES.forEach((o) => (extratosPorOriginador[o] = null));
    retornoData = null;
    sbDelete("extratos").catch(() => {});
    sbDelete("extrato").catch(() => {});
    sbDelete("retorno").catch(() => {});

    ORIGINADORES.forEach((o) => {
      const slug = slugOriginador(o);
      const fnEl = document.getElementById(`extrato-filename-${slug}`);
      if (fnEl) fnEl.textContent = "Clique ou arraste o .xlsx aqui";
      const labelEl = document.querySelector(`label[for="input-extrato-${slug}"]`);
      if (labelEl) labelEl.classList.remove("loaded");
      const inputEl = document.getElementById(`input-extrato-${slug}`);
      if (inputEl) inputEl.value = "";
    });
    document.getElementById("retorno-filename").textContent = "Clique ou arraste o arquivo .csv aqui";
    document.querySelector('label[for="input-retorno"]').classList.remove("loaded");
    document.getElementById("input-retorno").value = "";

    renderAll();
    showToast("Dados removidos.", "info");
  });
}

function applyLoadedExtrato(originador) {
  const d = extratosPorOriginador[originador];
  if (!d) return;
  const slug = slugOriginador(originador);
  const fnEl = document.getElementById(`extrato-filename-${slug}`);
  if (fnEl) fnEl.textContent = "✅ Extrato carregado";
  const labelEl = document.querySelector(`label[for="input-extrato-${slug}"]`);
  if (labelEl) labelEl.classList.add("loaded");
}

function applyAllLoadedExtratos() {
  ORIGINADORES.forEach((o) => applyLoadedExtrato(o));
}

function applyLoadedRetorno() {
  if (!retornoData) return;
  localStorage.setItem(STORAGE_RETORNO, JSON.stringify(retornoData));
  document.getElementById("retorno-filename").textContent = "✅ Retorno carregado";
  document.querySelector('label[for="input-retorno"]').classList.add("loaded");
}

function loadFromStorage() {
  // Carrega localStorage imediatamente (sem bloquear a UI)
  try {
    const novo = JSON.parse(localStorage.getItem(STORAGE_EXTRATOS));
    if (novo) {
      ORIGINADORES.forEach((o) => (extratosPorOriginador[o] = novo[o] || null));
    } else {
      // Migração do formato antigo (extrato único) -> Vem Benefícios
      const legado = JSON.parse(localStorage.getItem(STORAGE_EXTRATO));
      if (legado && legado.rows && legado.rows.length) {
        legado.rows.forEach((r) => { if (!r.originador) r.originador = ORIGINADOR_RETORNO; });
        extratosPorOriginador[ORIGINADOR_RETORNO] = legado;
        localStorage.setItem(STORAGE_EXTRATOS, JSON.stringify(extratosPorOriginador));
      }
    }
  } catch { /* ignora */ }

  try { retornoData = JSON.parse(localStorage.getItem(STORAGE_RETORNO)); } catch { retornoData = null; }
  applyAllLoadedExtratos();
  applyLoadedRetorno();

  // Sincroniza com Supabase em background — atualiza se tiver dado mais recente
  Promise.all([sbLoad("extratos"), sbLoad("retorno"), sbLoad("extrato")])
    .then(([extratosVal, retVal, legadoVal]) => {
      let atualizado = false;

      if (extratosVal) {
        ORIGINADORES.forEach((o) => {
          const remoto = extratosVal[o];
          if (remoto && remoto.importadoEm !== extratosPorOriginador[o]?.importadoEm) {
            extratosPorOriginador[o] = remoto;
            atualizado = true;
          }
        });
      } else if (legadoVal && legadoVal.rows && legadoVal.rows.length && !extratosPorOriginador[ORIGINADOR_RETORNO]) {
        legadoVal.rows.forEach((r) => { if (!r.originador) r.originador = ORIGINADOR_RETORNO; });
        extratosPorOriginador[ORIGINADOR_RETORNO] = legadoVal;
        atualizado = true;
      }

      if (retVal && retVal.importadoEm !== retornoData?.importadoEm) {
        retornoData = retVal;
        applyLoadedRetorno();
        atualizado = true;
      }
      if (atualizado) {
        localStorage.setItem(STORAGE_EXTRATOS, JSON.stringify(extratosPorOriginador));
        applyAllLoadedExtratos();
        renderAll();
        showToast("Dados atualizados do servidor.", "info", 3000);
      }
    })
    .catch((err) => showToast("Erro Supabase: " + err.message, "error", 8000));
}

/* ---------------- Auth ---------------- */

function getSession() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_SESSION));
  } catch {
    return null;
  }
}

function applyRolePermissions(session) {
  document.body.classList.toggle("role-viewer", session.role === "viewer");
  document.getElementById("user-info-name").textContent = session.label;

  if (session.role === "viewer" && document.querySelector('.page#page-importar').classList.contains("active")) {
    navigateTo("home");
  }
}

function setupLogin() {
  const screen = document.getElementById("login-screen");
  const form = document.getElementById("login-form");
  const error = document.getElementById("login-error");

  const session = getSession();
  if (session && USERS[session.user]) {
    screen.hidden = true;
    applyRolePermissions(session);
  } else {
    screen.hidden = false;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const user = document.getElementById("login-user").value.trim();
    const pass = document.getElementById("login-pass").value;
    const account = USERS[user];

    if (!account || account.password !== pass) {
      error.hidden = false;
      return;
    }

    error.hidden = true;
    const newSession = { user, role: account.role, label: account.label };
    localStorage.setItem(STORAGE_SESSION, JSON.stringify(newSession));
    applyRolePermissions(newSession);
    screen.hidden = true;
    form.reset();
  });

  document.getElementById("btn-logout").addEventListener("click", () => {
    localStorage.removeItem(STORAGE_SESSION);
    location.reload();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupLogin();
  setupNavigation();
  setupMobileNav();
  setupUploads();
  setupFilters();
  setupExports();
  setupClear();
  loadFromStorage();
  renderAll();
});
