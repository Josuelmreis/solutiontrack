# SolutionTrack — Tracking, Atribuição UTM & Meta Ads

Plataforma completa de **tracking de vendas, lucro real e gestão de anúncios** (uma fusão entre UTMify e Facebook Ads Manager), escrita 100% em **Python puro** (sem `pip install`) + SPA em HTML/Tailwind.

```
SolutionTrack/
├── server.py          # Backend (http.server stdlib, SQLite, OAuth, webhooks, CAPI, regras)
├── index.html         # Painel SPA (dashboard, ads manager, financeiro, funil)
├── solutiontrack.js   # Script de pixel instalado na página de vendas
├── render.yaml        # Config de deploy no Render
└── requirements.txt   # Vazio (sem dependências)
```

## Rodar local

```bash
python server.py 10000
```

Acesse **http://localhost:10000**. O banco `solutiontrack.db` é criado automaticamente (migrações em `init_db()`).

---

# 📖 TUTORIAL PASSO A PASSO

## 1. Hospedar de graça no Render

1. **Crie o repositório no GitHub**
   - Crie um repo novo (ex.: `solutiontrack`).
   - Suba os 4 arquivos (`server.py`, `index.html`, `solutiontrack.js`, `render.yaml`).
   ```bash
   git init
   git add .
   git commit -m "SolutionTrack"
   git branch -M main
   git remote add origin https://github.com/SEU_USUARIO/solutiontrack.git
   git push -u origin main
   ```

2. **Crie o Web Service no Render**
   - Entre em [render.com](https://render.com) → **New +** → **Web Service** → conecte o GitHub e selecione o repo.
   - **Runtime:** Python 3
   - **Build Command:** *(deixe vazio)*
   - **Start Command:** `python server.py 10000`
   - **Instance Type:** Free
   - Clique em **Create Web Service**.

3. **Configure as variáveis de ambiente** (aba *Environment*):
   | Chave | Valor |
   |---|---|
   | `PUBLIC_BASE_URL` | `https://SEU-DOMINIO.onrender.com` (a URL que o Render te deu) |
   | `META_APP_ID` | (preenche depois, passo 2) |
   | `META_APP_SECRET` | (preenche depois, passo 2) |

   > `PUBLIC_BASE_URL` é essencial: é com ela que o sistema monta o *redirect* do OAuth e as URLs de webhook/pixel.

4. Aguarde o deploy. Abra a URL `.onrender.com` → o painel SolutionTrack carrega.

> ⚠️ O plano Free do Render **hiberna após inatividade**. A primeira requisição depois de hibernar pode levar ~30s.

---

## 2. Conectar a Meta via OAuth (1 clique — 60 dias)

1. Acesse **[developers.facebook.com](https://developers.facebook.com)** → **Meus Apps** → **Criar App**.
2. Tipo de app: **"Empresa"** (ou **"Outro"**). Dê um nome e crie.
3. No painel do app, adicione o produto **"Facebook Login para Empresas"** (ou "Facebook Login").
4. Em **Login do Facebook → Configurações**, no campo **URIs de redirecionamento do OAuth válidos**, cole:
   ```
   https://SEU-DOMINIO.onrender.com/api/integrations/meta/callback
   ```
5. Em **Configurações → Básico**, copie o **ID do App** e a **Chave Secreta do App**.
6. No painel SolutionTrack → aba **Webhooks & Config** → cole **App ID** e **App Secret** e salve (ou preencha as env vars `META_APP_ID` / `META_APP_SECRET` no Render).
7. Vá em **Contas Meta → Conectar com Facebook**. O popup pede apenas `ads_read` e `public_profile` (sem `ads_management`), então funciona **em modo Desenvolvimento, sem App Review**.
   - O sistema troca o `code` → token curto → **Long-Lived Token (60 dias)** automaticamente.

---

## 3. Gerar o Token Permanente (nunca expira)

Para um token que **não expira**, use um **Usuário do Sistema** da Business Manager:

1. Abra **[business.facebook.com/settings](https://business.facebook.com/settings)**.
2. Menu lateral → **Usuários → Usuários do sistema** → **Adicionar** → crie um usuário do tipo **Administrador**.
3. **Resolvendo "Nenhuma permissão disponível":**
   - Vá em **Contas → Aplicativos** → **Adicionar** → informe o **App ID** criado no passo 2.
   - Selecione o Usuário do Sistema e dê a ele **Controle total / Administrador** sobre o app.
   - (Também adicione as **Contas de Anúncio** desejadas ao Usuário do Sistema em *Contas → Contas de anúncios*.)
4. De volta em **Usuários do sistema**, selecione o usuário → **Gerar novo token**.
   - Escolha o **App**.
   - **Expiração: Nunca**.
   - Marque a permissão **`ads_read`**.
   - Clique em **Gerar token** e **copie** (ele só aparece uma vez).
5. No SolutionTrack → **Contas Meta → Token Permanente (Nunca Expira)** → cole o token → **Adicionar Manual**.
   - O sistema valida com `/me`, importa todas as contas de anúncio (com paginação) e marca `is_permanent = 1`.

---

## 4. Cadastrar os Webhooks e instalar o Pixel

### Webhooks das plataformas de vendas
No painel → **Webhooks & Config**, copie a URL de cada plataforma:

| Plataforma | URL do webhook |
|---|---|
| Kiwify | `https://SEU-DOMINIO.onrender.com/api/webhook/kiwify` |
| Hotmart | `https://SEU-DOMINIO.onrender.com/api/webhook/hotmart` |
| Greenn | `https://SEU-DOMINIO.onrender.com/api/webhook/greenn` |
| Cakto | `https://SEU-DOMINIO.onrender.com/api/webhook/cakto` |
| Wiapy | `https://SEU-DOMINIO.onrender.com/api/webhook/wiapy` |

- **Kiwify:** *Configurações → Webhooks → Adicionar* → cole a URL → eventos de compra aprovada/pendente.
- **Hotmart:** *Ferramentas → Webhook (API e Notificações)* → cole a URL.
- **Greenn / Cakto / Wiapy:** seção de *Integrações/Webhooks* → cole a URL correspondente.

O sistema normaliza o status (`approved` / `pending` / `refused`), extrai UTMs (`utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `src`, `sck`, `fbc`, `fbp`) e, se a venda for **aprovada**, dispara o evento **Purchase** via CAPI para os pixels ativos.

### Script de Pixel na página de vendas
Cole antes do `</body>` da sua página/checkout:
```html
<script src="https://SEU-DOMINIO.onrender.com/solutiontrack.js"></script>
```
O script:
- captura UTMs em **cookies + localStorage**;
- gera/mantém `_fbp` e `_fbc` (compatíveis com Meta CAPI);
- **decora automaticamente** os links de checkout (Kiwify, Hotmart, Greenn, Cakto, Wiapy) injetando UTMs e cookies;
- envia `PageView` e `InitiateCheckout` para `/api/track`.

---

## 5. Pixels & Conversions API (CAPI)

1. No **Gerenciador de Eventos** da Meta, copie o **Pixel ID** e gere um **Access Token** da Conversions API.
2. No SolutionTrack → **Pixels & CAPI** → cadastre o pixel (nome, Pixel ID, token, e opcionalmente o *Test Event Code*).
3. Use **Testar Disparo CAPI** para enviar um `Purchase` de teste e validar no *Gerenciador de Eventos → Testar eventos*.
   - Os dados sensíveis (e-mail, telefone, IP, user-agent, `_fbp`/`_fbc`) são enviados com **hash SHA-256** quando aplicável.

---

## 6. Regras & Automações

Em **Regras & Automações** crie regras que rodam em background (a cada 5 min):
- **Stop Loss:** pausar se gasto > R$ X e vendas = 0.
- **Escala:** aumentar orçamento se ROAS > meta.
- **Agendamento:** desligar de madrugada, religar de manhã.

O histórico de execuções fica registrado em `rule_logs`.

---

## Notas técnicas

- **Sem dependências externas** — só biblioteca padrão (`http.server`, `urllib`, `sqlite3`, `json`, `hashlib`, `uuid`, `datetime`).
- Servidor **ThreadingHTTPServer** com CORS liberado e shutdown gracioso (Ctrl+C).
- Tokens e segredos **nunca são expostos** em JSON para o frontend (o endpoint `/api/config` devolve apenas `has_secret: true/false`).
- Graph API **`v21.0`** com paginação completa (`fetch_all_meta_pages`).
