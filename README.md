# Extrato WEB — Painel de Conciliação Financeira

Aplicação web de página única (SPA) para **conciliação entre o extrato bancário e o arquivo de retorno do BPO**, por convênio e competência. Roda 100% no navegador, sem backend próprio: os arquivos são lidos localmente, os dados ficam no `localStorage` e são sincronizados entre usuários via Supabase.

Repositório: <https://github.com/AlvoCard-dev/conciliacao>

---

## Sumário

- [Visão geral](#visão-geral)
- [Stack](#stack)
- [Estrutura do projeto](#estrutura-do-projeto)
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
