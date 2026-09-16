# Importação automática no painel Extrato_WEB

Automação que valida os insumos diários e os importa no painel
<https://jdantasrpa.github.io/Extrato_WEB/> (aba **Importar Dados**),
sem mover nenhum arquivo.

## Fluxo

```
Início
  ↓
Validar arquivos obrigatórios  (gate único)
  ↓
Todos existem?
  ├── Não → registra CRÍTICA (lista os ausentes) → encerra
  └── Sim → publica os 2 arquivos no chat do Teams (best-effort)
             → autentica no painel → aba Importar Dados
             → envia Extrato bancário (VEM BENEFICIOS)
             → envia Arquivo Retorno BPO
             → confirma cada importação
             → Fim  (arquivos permanecem na origem)
```

## Arquivos obrigatórios

| Insumo | Origem |
|---|---|
| Extrato bancário | `extratos/VEM BENEFICIOS/consolidado_VEM BENEFICIOS.xlsx` |
| Arquivo Retorno BPO | `extratos/BPO/DDMMAAAA_HHMMSS_consolidacao_arquivo_retorno.csv` (mais recente) |

## Configuração de credenciais

O perfil **Master** é o único com acesso à importação. A senha **não**
fica no código. Escolha uma das opções:

1. Variáveis de ambiente (prioridade):
   - `ALVO_PAINEL_USUARIO=Master`
   - `ALVO_PAINEL_SENHA=...`
2. Arquivo `config.ini` (copie de `config.ini.exemplo`):
   ```ini
   [PAINEL]
   usuario = Master
   senha = ...
   ```

> Não versione o `config.ini` com a senha real.

## Publicação no Teams (antes da importação)

Os **mesmos dois arquivos** anexados no painel são publicados antes no
chat de grupo do Teams, para o time consumir o dado na origem. Feito pelo
módulo `notificacao_teams.py` via Microsoft Graph, com a mesma estrutura
de token do robô `fiducial_datacob_contratos_ativos`:

1. token MSAL (device code flow) — cache em
   `%LOCALAPPDATA%\rpa_alvocard\token_cache_email.json`, semeado na
   primeira execução a partir de
   `C:\RPA\fiducial_datacob_contratos_ativos\.token_cache_email.json`
   (não exige nova autenticação interativa);
2. `GET /me/chats` → localiza o chat pelo `topic` (comparação sem acento
   e sem distinção de caixa);
3. `PUT /me/drive/root:/Microsoft Teams Chat Files/...:/content` → sobe o
   arquivo (acima de 4 MB usa sessão de upload em blocos);
4. `POST /me/drive/items/{id}/createLink` (escopo `organization`) →
   libera a leitura para os membros do chat;
5. `POST /me/chats/{id}/messages` → publica a mensagem com os anexos.

Escopos do Graph: `Chat.ReadBasic`, `ChatMessage.Send`, `Files.ReadWrite`.

Configuração (`config.ini`, bloco opcional):

```ini
[TEAMS]
grupo = Extrato bancario
ativo = sim
```

Características:

- **Idempotente por data** — checkpoint `ultima_data_enviada_teams` em
  `extratos/estado_importacao.json`; reexecuções no mesmo dia não
  republicam.
- **Best-effort** — falha na publicação é registrada em log (`ERROR`) e a
  importação no painel prossegue normalmente.
- O arquivo sobe com o nome prefixado pela data
  (`13-08-2026_consolidado_VEM BENEFICIOS.xlsx`), então reexecuções do
  mesmo dia substituem o item e dias diferentes não invalidam links já
  publicados.

## Execução

```bash
python importar_extrato_web.py
```

Códigos de saída: `0` sucesso · `1` abortado no gate (falta insumo) ·
`2` erro na automação. Log em `extratos/log_importacao_web.txt`.

### Variáveis de ambiente

| Variável | Uso | Padrão |
|---|---|---|
| `ALVO_PAINEL_USUARIO` | Usuário do painel | `Master` |
| `ALVO_PAINEL_SENHA` | Senha (obrigatória se não vier do `config.ini`) | — |
| `ALVO_PAINEL_URL` | Sobrescreve a URL (para teste isolado) | produção |
| `ALVO_NAVEGADOR_PATH` | Caminho do `chrome.exe` | auto-detecção |
| `ALVO_TEAMS_GRUPO` | Nome (topic) do chat de grupo | `Extrato bancario` |
| `ALVO_TEAMS_ATIVO` | `nao` desliga a publicação no Teams | `sim` |

O Chrome é auto-detectado, inclusive na instalação por-usuário
(`%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe`). Só defina
`ALVO_NAVEGADOR_PATH` se estiver num caminho fora do comum.

### Teste isolado (recomendado antes de produção)

Sirva uma cópia local do painel com o Supabase neutralizado e aponte a
automação para ela via `ALVO_PAINEL_URL=http://localhost:PORTA/`. Assim o
fluxo (login → upload → confirmação) é validado sem gravar no Supabase
compartilhado de produção.

> Validado em produção em 04/08/2026: login, envio do extrato e do retorno
> confirmados (exit 0).

## Testes

```bash
python -m pytest tests/test_importar_extrato_web.py tests/test_notificacao_teams.py -q
```

Cobrem as funções puras (validação, montagem, payload do Teams) e os
localizadores/credenciais. A orquestração do envio ao Teams é testada com
o Graph simulado (sem rede).
A automação web em si (`importar_no_painel`) não é coberta por teste
unitário por depender do navegador.

## Premissas técnicas

- Os `<input type=file>` do painel são `hidden`; o script os revela via
  `executar_script` antes do envio (evita `ElementNotInteractable`).
- O upload usa `web_utils.escrever_em_elemento` (que, com `performar=False`,
  faz apenas `send_keys` do caminho — sem click/clear — não abrindo o
  diálogo nativo do SO).
- O detector de arquivos do Selenium é trocado por `UselessFileDetector`
  (`_configurar_upload_local`): sem isso, `send_keys` de um caminho tenta o
  upload remoto (`se/file`), não suportado pelo chromedriver local.
- A confirmação da importação é detectada pela mudança do rótulo do
  dropzone (`#extrato-filename-...` / `#retorno-filename`).
- A importação publica os dados no Supabase compartilhado do painel
  (efeito colateral esperado do fluxo).
