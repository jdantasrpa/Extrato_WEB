# Extrato WEB — Painel de Conciliação Financeira

Aplicação web de página única (SPA) para **conciliação entre o extrato bancário e o arquivo de retorno do BPO**, por convênio e competência. Roda 100% no navegador, sem backend próprio: os arquivos são lidos localmente, os dados ficam no `localStorage` e são sincronizados entre usuários via Supabase.

Repositório: <https://github.com/jdantasrpa/Extrato_WEB>

---

## Sumário

- [Visão geral](#visão-geral)
- [Stack](#stack)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Como o app.js funciona](#como-o-appjs-funciona)
- [Como executar](#como-executar)
- [Autenticação e perfis](#autenticação-e-perfis)
- [Arquivos de entrada](#arquivos-de-entrada)
- [Regra de conciliação](#regra-de-conciliação)
- [Telas](#telas)
- [Persistência e sincronização](#persistência-e-sincronização)
- [Exportações](#exportações)
- [Configuração](#configuração)
- [Limitações conhecidas](#limitações-conhecidas)

---

## Visão geral

O fluxo do painel é:

```
Extrato .xlsx (por originador) ─┐
                                ├─► Normalização ─► Agregação (convênio × mês) ─► Conciliação ─► Dashboard / Exportação
Arquivo Retorno .csv (BPO) ─────┘
```

O usuário importa os arquivos na tela **Importar Dados**; todas as demais telas (Visão Geral, Ranking, Evolução, Retorno e Conciliação) são derivadas desses dois insumos e recalculadas a cada renderização.

## Stack

Sem build, sem gerenciador de pacotes — tudo via CDN:

| Biblioteca | Uso |
|---|---|
| [SheetJS (xlsx) 0.18.5](https://sheetjs.com) | Leitura do extrato `.xlsx` e geração dos arquivos exportados |
| [PapaParse 5.4.1](https://www.papaparse.com) | Parsing do arquivo de retorno `.csv` |
| [Chart.js 4.4.4](https://www.chartjs.org) | Donuts, barras e séries temporais |
| [supabase-js 2](https://supabase.com/docs/reference/javascript) | Persistência compartilhada (tabela `app_state`) |
| Google Fonts (Inter + JetBrains Mono) | Tipografia |

## Estrutura do projeto

```
Extrato_WEB/
├── index.html   # markup completo — login, sidebar e as 6 seções de página
├── app.js       # toda a lógica: import, normalização, conciliação, render, export, auth
├── style.css    # tema dark navy, layout responsivo, componentes (KPI, painel, tabela, toast)
└── logo.png     # marca usada no login, topbar e sidebar
```

O `app.js` é organizado em blocos comentados: `Supabase`, `Helpers`, `Navigation`, `Toasts & Loading`, `Extrato (xlsx)`, `Retorno (csv)`, `Render: <página>`, `Charts`, `Conciliação`, `Setup`, `Export` e `Auth`. A inicialização acontece no `DOMContentLoaded` ao final do arquivo.

## Como o app.js funciona

Sem framework, sem build, sem módulos: um único arquivo de ~1.680 linhas carregado por `<script>` no fim do `index.html`, operando direto no DOM. O desenho se apoia em três decisões que explicam quase todo o resto do código:

1. **Estado global em memória** — duas variáveis concentram tudo que foi importado.
2. **Re-render total** — qualquer mudança (import, filtro, sincronização) redesenha as cinco páginas inteiras. Não há renderização incremental nem *diffing*.
3. **O DOM é a fonte dos filtros** — o valor dos `<select>` é lido no momento de renderizar, em vez de ser espelhado em variáveis de estado.

### Ciclo de vida

```
DOMContentLoaded
      │
      ├─ setupLogin()        valida sessão do localStorage; mostra ou esconde o overlay de login
      ├─ setupNavigation()   liga os botões da sidebar → navigateTo(page)
      ├─ setupMobileNav()    hambúrguer e overlay no mobile
      ├─ setupUploads()      gera a grade de dropzones a partir de ORIGINADORES
      ├─ setupFilters()      liga cada <select> ao render da sua página
      ├─ setupExports()      liga os botões de exportação
      ├─ setupClear()        liga o botão "Limpar dados"
      ├─ loadFromStorage()   localStorage (síncrono) → Supabase (background)
      └─ renderAll()         primeira pintura de todas as páginas
```

As funções `setup*` rodam **uma única vez** e apenas registram listeners. Toda a lógica visível depois disso passa por `renderAll()`.

### Estado global

| Variável | Conteúdo |
|---|---|
| `extratosPorOriginador` | `{ [originador]: { convCol, dataCol, headers, rows, importadoEm } \| null }` |
| `retornoData` | `{ rows: [{ convenio, competencia, valor }], importadoEm }` ou `null` |
| `chartCreditoDebito`, `chartEvolucao`, … | Instâncias do Chart.js, guardadas para poder destruí-las antes de recriar |

Os acessos ao extrato passam por quatro funções auxiliares em vez de tocar o objeto direto — `getExtratoRows(originador?)` (concatena as linhas de um ou de todos), `hasAnyExtrato()`, `countExtratoLancamentos()` e `latestExtratoImportadoEm()`. É o que permite a lista `ORIGINADORES` crescer ou encolher sem mexer no resto do código.

### Do arquivo à tela

```
handleExtratoFile(file, originador)          handleRetornoFile(file)
  file.arrayBuffer()                           file.arrayBuffer()
  XLSX.read(cellDates: true)                   TextDecoder('windows-1252')
  sheet_to_json()                              Papa.parse(delimiter: ';', header: true)
  detecta colunas por heurística               mapeia 3 colunas fixas
  normaliza cada linha                         parseNumeroBR no valor
  snapshot dos totais anteriores               │
  │                                            │
  └──────────────┬─────────────────────────────┘
                 ▼
     extratosPorOriginador / retornoData   (memória)
                 ▼
     localStorage.setItem(...)             (síncrono, imediato)
                 ▼
     sbSave(...)                           (assíncrono, não bloqueia — toast em caso de erro)
                 ▼
     renderAll()
```

O `sbSave` é deliberadamente *fire-and-forget*: a interface não espera a rede. Se a gravação remota falhar, o dado continua salvo localmente e um toast informa o erro.

### Modelo de dados normalizado

Cada linha do extrato vira este objeto — é a estrutura que todas as telas consomem:

```js
{
  conv:       "PREFEITURA MUNICIPAL X",  // valor da coluna de convênio
  data:       "2026-05-14T00:00:00.000Z", // ISO, ou null se a data for inválida
  mesAno:     "05/2026",                  // derivado da data, chave de agregação
  valorD:     0,                          // valor se natureza === "D", senão 0
  valorC:     1520.30,                    // valor se natureza === "C", senão 0
  originador: "Vem Benefícios",
  raw:        { /* linha original completa da planilha */ }
}
```

Duas decisões importantes aqui:

- **Crédito e débito viram colunas, não um campo `natureza`.** Isso transforma toda agregação em soma simples (`reduce`), sem condicionais espalhadas pelo código.
- **`raw` preserva a linha original.** É o que permite a aba "Extrato" da exportação de conciliação devolver os lançamentos exatamente como vieram da planilha.

### O ciclo de renderização

```js
function renderAll() {
  safeRender(renderHome);
  safeRender(renderRanking);
  safeRender(renderEvolucao);
  safeRender(renderRetorno);
  safeRender(renderConciliacao);
  safeRender(updateStatusPills);
}
```

`safeRender` envolve cada chamada em `try/catch` com `console.error`: um erro numa página não impede as outras de pintar. Todas as funções `render*` seguem o mesmo roteiro:

1. Limpar o `<tbody>` (`innerHTML = ""`)
2. Repovoar os `<select>` de filtro com `fillSelect`, que **preserva a opção escolhida** se ela ainda existir nos dados novos
3. Ler os filtros direto do DOM
4. Filtrar e agregar as linhas com um `Map` chaveado por convênio ou por `convênio__mês`
5. Montar as `<tr>` e, quando não há dados, esconder a tabela e exibir o `.empty-state`

Como o estado dos filtros vive no DOM, `renderAll()` pode ser chamado de qualquer lugar — inclusive pela sincronização do Supabase chegando em segundo plano — sem que a seleção do usuário se perca.

### Gráficos

Todo gráfico segue o mesmo protocolo: destruir a instância anterior, checar se o Chart.js carregou, checar se há dados, e só então criar. Sem o `destroy()`, o Chart.js empilha instâncias sobre o mesmo `<canvas>` e os *tooltips* passam a responder duas vezes.

Há um plugin próprio registrado uma única vez, o **`centerText`**, que desenha valor e rótulo no vazio central dos gráficos de rosca — usado para o saldo no donut de Crédito × Débito e para a taxa percentual nos donuts de status.

Os dois donuts de status (Visão Geral e Conciliação) compartilham a mesma função `renderStatusDonut(canvasId, emptyId, dados, chartExistente)`, que devolve a nova instância para ser guardada na variável correspondente.

### Mapa do arquivo

| Bloco | Funções principais | Responsabilidade |
|---|---|---|
| Supabase | `sbSave`, `sbLoad`, `sbDelete` | Leitura e escrita na tabela `app_state` |
| Helpers | `moeda`, `moedaCompacta`, `parseNumeroBR`, `normalizeNome`, `competenciaParaMesAno`, `mesAnoProximoMes`, `fillSelect` | Formatação e conversão — **funções puras** |
| Navigation | `navigateTo`, `setupMobileNav` | Troca de `.page` ativa e menu mobile |
| Toasts & Loading | `showToast`, `showLoading`, `hideLoading` | Feedback visual |
| Import | `handleExtratoFile`, `handleRetornoFile` | Parsing e normalização dos arquivos |
| Render | `renderHome`, `renderRanking`, `renderEvolucao`, `renderRetorno`, `renderConciliacao` | Uma função por página |
| Charts | `renderChart*`, `renderStatusDonut` | Instanciação e destruição dos gráficos |
| Conciliação | `computeConciliacao`, `filtrarConciliacao`, `updateConciliacaoSummary` | Cruzamento Retorno × Extrato — ver [Regra de conciliação](#regra-de-conciliação) |
| Setup | `setupUploads`, `setupDropzone`, `setupFilters`, `setupClear`, `updateStatusPills` | Registro de listeners, executado uma vez |
| Export | `exportTableToExcel`, `exportConciliacaoExcel` | Geração dos `.xlsx` |
| Auth | `getSession`, `setupLogin`, `applyRolePermissions` | Login, sessão e perfil `viewer` |

### Convenções e armadilhas ao mexer

- **`computeConciliacao()` é recalculada do zero a cada render**, inclusive dentro de `renderHome`. É uma função pura sobre o estado global — barata o bastante nesse volume, mas é o primeiro lugar a otimizar se a base crescer.
- **A grade de importação é gerada por JS**, não existe no HTML. Os `id` dos elementos são derivados de `slugOriginador(nome)` (sem acento, espaços viram hífen). Ao adicionar um originador, basta incluí-lo em `ORIGINADORES`.
- **Linhas de tabela são montadas com `innerHTML` e template literals**, interpolando valores vindos das planilhas sem escape. Ao editar qualquer `render*`, tenha em mente que conteúdo da planilha é interpretado como HTML — ver [Limitações conhecidas](#limitações-conhecidas).
- **Só o originador em `ORIGINADOR_RETORNO` entra na conciliação.** As demais telas somam todos os originadores importados; confundir os dois escopos é o erro mais fácil de cometer aqui.
- **Alterou o `app.js`?** Atualize o `?v=` no `<script>` do `index.html`, senão o navegador serve a versão em cache.

## Como executar

O projeto é estático. Como os imports usam `fetch`/`FileReader` e o Supabase, **sirva por HTTP** em vez de abrir o `index.html` direto pelo sistema de arquivos:

```bash
# Python
python -m http.server 8000

# Node
npx serve .
```

Depois acesse <http://localhost:8000>.

Ao alterar o `app.js`, atualize o cache-buster no final do `index.html` (`<script src="app.js?v=6">`) para evitar script obsoleto após o deploy.

## Autenticação e perfis

Login simples de front-end, com sessão persistida em `localStorage` (`alvo_card_session_v1`). Há dois perfis:

| Perfil | Papel | Acesso |
|---|---|---|
| `Master` | `master` | Acesso total, incluindo **Importar Dados** |
| `AlvoCard` | `viewer` | Somente leitura — a classe `role-viewer` no `<body>` oculta a importação |

> ⚠️ Esta autenticação é apenas uma barreira de conveniência. As credenciais ficam no `USERS` do `app.js` e são visíveis para qualquer pessoa com acesso ao arquivo. Veja [Limitações conhecidas](#limitações-conhecidas).

## Arquivos de entrada

### Extrato bancário (`.xlsx`, um por originador)

As colunas são detectadas por heurística sobre o cabeçalho da primeira planilha:

| Coluna esperada | Como é localizada | Uso |
|---|---|---|
| Data Movimentação | contém `data` **e** `mov` | Deriva o `MM/AAAA` do lançamento |
| Convênio | contém `conv` | Chave de agrupamento |
| Valor | igual a `valor` | Valor do lançamento |
| Natureza | contém `natureza` | `C` → crédito, `D` → débito |

Cada linha é normalizada para `{ conv, data, mesAno, valorD, valorC, originador, raw }`. O objeto `raw` preserva a linha original e é reaproveitado na aba **Extrato** da exportação de conciliação.

### Arquivo Retorno BPO (`.csv`)

- Encoding **windows-1252**, delimitador `;`, com cabeçalho.
- Colunas usadas: `convenio`, `competencia`, `total_valor_descontado`.
- `competencia` aceita `04/2026`, `4/26` ou `abr/26` (meses em português abreviados).

## Regra de conciliação

O cruzamento une as duas bases pela chave `convênio normalizado + mês`:

1. **Normalização do convênio** — sem acentos, minúsculas, espaços colapsados.
2. **Defasagem de competência** — a competência do retorno é comparada com o **mês seguinte** no extrato (`abr/26` → `05/2026`), refletindo o prazo de repasse.
3. **Comparação** — `diferença = crédito do extrato − valor descontado no retorno`.

Status resultante:

| Status | Critério |
|---|---|
| **Conciliado** | \|diferença\| < R$ 0,01 |
| **Conciliado a maior** | diferença positiva dentro de 5% do valor de retorno |
| **Conciliado a menor** | diferença negativa dentro de 5% do valor de retorno |
| **Divergente** | diferença acima de 5% do valor de retorno |
| **Sem Extrato** | chave existe apenas no arquivo de retorno |
| **Sem Retorno** | chave existe apenas no extrato |

A conciliação usa somente o originador definido em `ORIGINADOR_RETORNO` (hoje `Vem Benefícios`), que é a única origem cruzada com o BPO. As demais telas consideram todos os originadores importados.

## Telas

| Tela | Conteúdo |
|---|---|
| **Visão Geral** | 6 KPIs (crédito, débito, saldo, convênios ativos, taxa de conciliação, pendências) com variação vs. a importação anterior; donut Crédito × Débito; Top 5 convênios por saldo; donut de status; Top 5 divergências |
| **Importar Dados** | Dropzones por originador (`.xlsx`) + dropzone do retorno (`.csv`) e cards de status de cada fonte |
| **Ranking** | Totais de D, C e saldo por convênio, com linha de TOTAL; filtros de originador e convênio |
| **Evolução** | Série por convênio e mês, com filtro de período (De/Até) e gráfico de linha |
| **Arquivo Retorno** | Valores descontados por convênio e competência |
| **Conciliação** | Resumo por status, donut, comparativo Retorno × Extrato e tabela detalhada com filtros de convênio, mês e status |

## Persistência e sincronização

Chaves de `localStorage`:

| Chave | Conteúdo |
|---|---|
| `alvo_card_extratos_v2` | Extratos por originador (formato atual) |
| `alvo_card_extrato_v1` | Formato legado de extrato único — migrado automaticamente para `Vem Benefícios` |
| `alvo_card_retorno_v1` | Arquivo de retorno importado |
| `alvo_card_extrato_snapshot_v1` | Totais da importação anterior, usados nos deltas dos KPIs |
| `alvo_card_session_v1` | Sessão do usuário logado |

O Supabase espelha os mesmos dados na tabela `app_state` (`key`, `value`, `updated_at`), via `upsert` com conflito em `key`. Na carga da página o `localStorage` é aplicado imediatamente (UI não bloqueia) e, em segundo plano, o registro remoto é comparado pelo `importadoEm` — se for mais recente, substitui o local e a interface é re-renderizada. É isso que permite que uma importação feita por um usuário apareça para os demais.

O botão **Limpar dados** remove todas as chaves locais e os registros remotos correspondentes.

## Exportações

Todas geradas pelo SheetJS, no cliente:

| Botão | Arquivo |
|---|---|
| Ranking → Exportar Excel | `ranking_extrato.xlsx` |
| Evolução → Exportar Excel | `evolucao_extrato.xlsx` |
| Retorno → Exportar Excel | `arquivo_retorno.xlsx` |
| Conciliação → Exportar Excel | `conciliacao.xlsx` — aba **Conciliação** (respeitando os filtros ativos) + aba **Extrato** com os lançamentos originais das chaves exibidas |

## Configuração

Ajustes ficam no topo do `app.js`:

```js
const ORIGINADORES = ["Vem Benefícios"];        // originadores com dropzone de extrato
const ORIGINADOR_RETORNO = "Vem Benefícios";    // única origem cruzada com o BPO
const TOLERANCIA = 0.01;                        // tolerância, em reais, para status "Conciliado"
```

`AlvoCard`, `EiCard` e `Juntos Card` foram removidos temporariamente do array `ORIGINADORES` — para reativá-los, basta adicioná-los de volta; a grade de importação e todos os filtros são gerados a partir dessa lista.

## Limitações conhecidas

- **Credenciais e chave Supabase no cliente.** `USERS`, `SUPABASE_URL` e a chave anônima estão no `app.js`. Qualquer pessoa com acesso ao arquivo consegue lê-los. A proteção real precisa vir de RLS na tabela `app_state` e de autenticação server-side (Supabase Auth) — o login atual não impede acesso direto aos dados.
- **Renderização por `innerHTML`.** Linhas de tabela são montadas por interpolação de string com valores vindos das planilhas, sem escape. Conteúdo malicioso em uma célula pode ser interpretado como HTML.
- **Detecção de colunas por heurística.** Mudanças no cabeçalho do extrato (ex.: "Dt. Movimento" em vez de "Data Movimentação") fazem a coluna não ser encontrada, sem erro explícito.
- **Volume de dados.** Todo o extrato é mantido em memória e serializado no `localStorage`, que tem limite prático de poucos MB por origem.
- **Sem testes automatizados.** As regras de conciliação (`computeConciliacao`, `competenciaParaMesAno`, `mesAnoProximoMes`, `parseNumeroBR`) são puras e seriam os primeiros candidatos a cobertura.
