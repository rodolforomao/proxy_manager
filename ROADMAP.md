# Roadmap — Proxy Manager

## O que está implementado hoje

### Infraestrutura central
- **`models.py`** — `AppRule`, `ProxySettings`, `ProcessInfo` como dataclasses tipadas
- **`config.py`** — persistência JSON com migração versionada (v1 → v11)
- **`proxy_env.py`** — injeção de `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` / `NODE_EXTRA_CA_CERTS` por processo
- **`network.py`** — listagem de interfaces (Wi-Fi, Ethernet, Virtual), detecção de interface por PID
- **`service_status.py`** — registro central thread-safe de status de serviços com listener pattern e log global (200 linhas)
- **`profiles.py`** — snapshots nomeados de configuração de proxy (salvar / carregar / excluir)

### Proxy local
- **`local_proxy.py`** — gost v3.0.0 (go-gost) + fallback pproxy
  - Download automático (tar.gz, x86_64 e arm64)
  - PID file + kill gracioso (SIGTERM → SIGKILL)
  - Log em `~/.config/proxy-manager/local-proxy.log`
  - **Watchdog** (daemon 12s): reinicia gost automaticamente se cair, notifica GUI e desktop
  - Modo direto (`-L` sem `-F`), modo upstream (`-F <url>`)

### Fontes de proxy
- **Personalizado** — host / porta / usuário / senha manuais
- **Gratuito** — busca listas públicas (TheSpeedX, clarketm, monosans) + teste paralelo
- **Pago** — templates: Smartproxy, Bright Data, Oxylabs, Webshare, IPRoyal, Custom
- **Tor** — SOCKS5 do sistema (`:9050`) ou instância gerenciada (`:9051`); exit node por país
- **Direto** — sem upstream (diagnóstico)

### Auto-configuração
- Modo **⚡ Rápido** — busca + testa proxy público, aceita candidato
- Modo **🧅 Tor** — inicia Tor gerenciado ou usa Tor do sistema
- Seletor de **país alvo** — filtra até achar saída no país escolhido
- Troca de modo sem reiniciar o app

### Gerenciamento de apps
- **Catálogo padrão** (`presets.py`) — Claude Code, Cursor, VS Code, Chrome, Firefox, Discord, Slack, Telegram, Spotify e outros
- Categorias: `ai`, `browser`, `dev`, `social`, `media`, `tools`, `custom`
- Toggle proxy por app, editar, remover (apps custom)
- **Buscar app instalado** — lê `.desktop` de `/usr/share/applications`, `~/.local/share/applications`, Flatpak/Snap; picker com busca em tempo real
- **Upstream por app** — campo `upstream_proxy` em `AppRule`; bypass do proxy global por app
- **Lançamento** com env vars de proxy injetadas no processo filho
- **Relançamento** — encerra e reinicia com ou sem proxy
- **Interface de rede por app** — tiles (⚡ Auto / 📶 Wi-Fi / 🔌 Cabo)

### Suporte a navegadores
- **Firefox** — reescreve `user.js` no perfil padrão, limpa lock antes de reiniciar
- **Chrome/Chromium** — injeta `--proxy-server=http://127.0.0.1:7890`

### Integração Claude Code
- **`claude_proxy.py`** — lê/escreve `~/.claude/settings.json` → seção `env`

### Tor avançado
- **`tor_country.py`** — `torrc` com `ExitNodes {CC} StrictNodes 1`, instância própria em `:9051`

### Roteamento por interface
- **`launcher.py`** — `systemd-run --scope -pBindInterfaces=<iface>`; fallback `ip netns`
- `scripts/setup-network.sh` — configura permissões via `pkexec`

### Monitoramento de processos
- **`process_monitor.py`** — scan via `psutil`, match por nome/cmdline
- **`process_cache.py`** — cache com refresh assíncrono e debounce
- Aba **Processos ativos** — tabela (PID, proxy, IP, rede, status, comando); ações inline

### Verificação e saúde
- **`proxy_health.py`** — IP público via proxy e direto, geo lookup (`ip-api.com`), comparação
- Display de IP + badge de país `[BR]` no header

### Resiliência e ciclo de vida
- **`main.py`** — instância única (flock), `atexit` + `SIGTERM/SIGINT` → `stop_watchdog` + `stop_local_proxy`
- **Reset de emergência** — para gost, encerra apps com proxy ativo, limpa Firefox/Claude, toast de confirmação

### GUI (customtkinter)
- Tema escuro, janela 1100×720
- **Header** — interruptor global, tiles de modo, seletor de país, IPs com badge de país
- **Barra de info do proxy** — local:porta → upstream, modo, PID, n° de apps com proxy
- **Barra de acesso rápido** — tiles clicáveis dos apps destaque
- **Aba Aplicativos** — lista por seção (proxy ativo / recentes / categoria), filtro de busca, scroll por hover
- **Aba Processos ativos** — tabela completa, ações em lote
- **Aba Configurações** — painéis por fonte, perfis de proxy, NO_PROXY, CA extra
- **Aba Serviços** — 7 cards de serviço (gost, watchdog, auto-config, scanner, tor, interfaces, tray) + log global
- **Toast inline** — notificações flutuantes no rodapé (info/warning/error/success, auto-dismiss 5s)
- **Notificações desktop** — `notify-send` via `notifications.py` (non-blocking)
- **Tray** — `pystray` com estado do proxy; fallback gracioso se não instalado

---

## Próximos passos — por prioridade

### P0 — Bugs e estabilidade imediata

| Item | Por quê |
|------|---------|
| **Fechar o app limpa proxy do Firefox/Chrome** | `on_closing` reinicia gost em modo direto, mas browsers abertos com `--proxy-server` precisam ser encerrados ou o Chrome fica sem rede após fechar o manager. |
| **Scroll por hover no picker de apps instalados** | Funciona via `win.bind_all` mas precisa de validação em múltiplos ambientes (Wayland vs X11). |
| **Testes de integração para reset de emergência** | O reset agora encerra processos e para gost; sem teste automatizado pode regredir silenciosamente. |

### P1 — UX de alto impacto

| Item | Por quê |
|------|---------|
| **Autostart na sessão do usuário** | Gerar entrada `.desktop` em `~/.config/autostart/` ou unit `systemd --user`. Hoje o usuário precisa abrir o app manualmente. |
| **Personalizar barra de acesso rápido** | `FEATURED_APP_IDS` é hardcoded. Deixar o usuário fixar seus apps favoritos com drag-or-pin. |
| **Ícone da janela (`.desktop` entry)** | Rodar `python main.py` não mostra ícone no dock/taskbar. Criar `proxy-manager.desktop` com ícone SVG. |
| **Import / Export de config** | Backup e compartilhamento do `config.json` via diálogo de arquivo (útil ao trocar de máquina). |
| **Wizard de primeiro uso** | Ao abrir pela 1ª vez: escolha de fonte → teste → primeiro app. Evita a confusão com a aba Configurações. |

### P2 — Qualidade e confiabilidade

| Item | Por quê |
|------|---------|
| **Timeout no scanner de processos** | `psutil.process_iter` sem timeout trava se um processo parar de responder. Adicionar `timeout=0.1` por processo. |
| **Histórico de proxy** | Log de quando proxy ligou/desligou e qual IP foi usado. Útil para auditoria e debugging. |
| **Brave / Edge / Chromium** | Mesmo mecanismo do Chrome (`--proxy-server`), mas precisa detectar o executável correto. |
| **Electron apps (Discord, Slack, VS Code)** | Usam Chromium interno; injetar `--proxy-server` funciona mas precisa validação por app. |
| **Validar SOCKS5 com autenticação** | `proxy_env.py` suporta `socks5h://user:pass@host:port` mas o formato não foi testado com gost v3. |

### P3 — Expansão de plataforma

| Item | Por quê |
|------|---------|
| **DNS leak prevention** | Proxy via env var não intercepta DNS. Redirecionar para DoH ou `socks5h` no gost evita vazamento. |
| **Múltiplos perfis Firefox** | Hoje gerencia só o perfil padrão. Suporte a múltiplos perfis e modo container. |
| **IPv6** | Toda detecção de IP e roteamento é IPv4. Dual-stack pode vazar o IP real. |
| **macOS (futuro)** | Depende de `/proc`, `psutil` Linux-específico, `systemd-run`. Precisaria: `launchctl`, `networksetup`, `dscl`. |

### P4 — Segurança e privacidade

| Item | Por quê |
|------|---------|
| **Armazenamento seguro de credenciais** | Usuário/senha ficam em `config.json` em texto plano. Usar `libsecret` / `keyring` do sistema. |
| **Verificação de integridade do gost** | Download sem verificação de hash/assinatura. Risco de supply chain se o GitHub for comprometido. |
| **Auditoria de vazamento** | Botão "testar vazamento": verifica se DNS, WebRTC e IP coincidem com o esperado via proxy. |

---

## Débito técnico

| Item | Arquivo | Problema |
|------|---------|----------|
| `_SYSTEMD_BIND` no import | `launcher.py:33` | Executado em todo import; mover para lazy init. |
| Migrações em cadeia | `config.py` | `if from_version < N` aninhado fica frágil. Migrar para dict de funções. |
| `CTkFont` recriado por card | `gui.py` | Algumas instâncias criam fonte no render em vez de reutilizar. |
| `scan_processes` sem timeout | `process_monitor.py:39` | `psutil.process_iter` pode bloquear. Adicionar timeout por processo. |
| Free proxy URLs hardcoded | `proxy_sources.py` | Se repositório sair do ar, a busca silencia. Externalizar para config. |
| `gui.py` monolítico (4000+ linhas) | `gui.py` | Difícil de manter. Extrair abas em módulos: `gui_apps.py`, `gui_procs.py`, `gui_settings.py`, `gui_services.py`. |

---

## Arquitetura sugerida para próximas versões

```
proxy_manager/
  core/
    models.py
    config.py
    migrator.py        ← extrair migrações de config.py
  proxy/
    local_proxy.py
    proxy_env.py
    proxy_sources.py
    proxy_health.py    ← adicionar DNS leak check
  apps/
    presets.py
    launcher.py        ← abstrair OS (Linux / macOS)
    process_monitor.py ← adicionar timeout
    browser_proxy.py   ← adicionar Brave/Edge/Electron
    claude_proxy.py
  network/
    network.py
    tor_country.py
  ui/
    gui.py             ← orquestrador, mantém estado
    gui_apps.py        ← aba Aplicativos
    gui_procs.py       ← aba Processos ativos
    gui_settings.py    ← aba Configurações
    gui_services.py    ← aba Serviços
    tray.py
    notifications.py
    wizard.py          ← onboarding de primeiro uso
  scripts/
    install.sh
    setup-network.sh
    launch_on_iface.sh
```

---

_Última atualização: 2026-06-30_
