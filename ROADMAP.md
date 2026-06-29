# Roadmap — Proxy Manager

## O que está implementado hoje

### Infraestrutura central
- **`models.py`** — `AppRule`, `ProxySettings`, `ProcessInfo` como dataclasses tipadas
- **`config.py`** — persistência JSON com sistema de migração versionado (v1 → v10)
- **`proxy_env.py`** — injeção de `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` / `NODE_EXTRA_CA_CERTS` no ambiente do processo
- **`network.py`** — listagem de interfaces de rede (Wi-Fi, Ethernet, Virtual), detecção de interface por PID via conexões TCP

### Proxy local
- **`local_proxy.py`** — baixa e gerencia o binário `gost` v2.11.5 (fallback para `pproxy`)
  - Download automático para x86_64 e arm64
  - PID file + kill gracioso (SIGTERM → SIGKILL)
  - Log em `~/.config/proxy-manager/gost.log`

### Fontes de proxy
- **Personalizado** — host / porta / usuário / senha manuais
- **Gratuito** — busca listas públicas (TheSpeedX, clarketm, monosans) + teste de conectividade em paralelo
- **Pago** — templates pré-configurados: Smartproxy, Bright Data, Oxylabs, Webshare, IPRoyal, Custom
- **Tor** — usa SOCKS5 do Tor do sistema (`:9050`) ou instância gerenciada (`:9051`)
- **Direto** — passa pelo proxy local sem upstream (útil para diagnóstico)

### Auto-configuração
- Modo **⚡ Rápido** — busca proxy público funcional, testa conectividade, aceita candidato
- Modo **🧅 Tor** — inicia Tor gerenciado ou usa Tor do sistema
- Seletor de **país alvo** — filtra candidatos até achar saída no país escolhido
- Troca de modo sem reiniciar o app (para proxy local, configura, verifica IP, relança apps ativos)

### Gerenciamento de apps
- **Catálogo padrão** (`presets.py`) — Claude Code, Cursor, VS Code, Chrome, Firefox, Discord, Slack, Telegram, Spotify e outros
- Categorias: `ai`, `browser`, `dev`, `social`, `media`, `tools`, `custom`
- Por app: ligar proxy, parar proxy, editar, remover (apps custom)
- **Lançamento** com variáveis de proxy injetadas no ambiente do filho
- **Relançamento** — encerra processo existente e relança com ou sem proxy
- **Interface de rede por app** — selector visual de tiles (⚡ Auto / 📶 Wi-Fi / 🔌 Cabo) com numeração quando há múltiplas interfaces

### Suporte a navegadores
- **Firefox** — reescreve `user.js` no perfil padrão com `network.proxy.*`, preserva o restante
- **Chrome/Chromium** — injeta `--proxy-server=http://127.0.0.1:7890` na linha de comando
- Limpa lock do perfil Firefox antes de reiniciar

### Integração Claude Code
- **`claude_proxy.py`** — lê/escreve `~/.claude/settings.json` na seção `env`
- Detecta proxy ativo via settings.json (não via `/proc`)

### Tor avançado
- **`tor_country.py`** — escreve `torrc` com `ExitNodes {CC} StrictNodes 1`
- Gerencia instância própria do Tor em `:9051` separada do sistema
- Flag `TOR_EXIT_COUNTRY_ENABLED = False` (pronto mas desligado, aguardando testes)

### Roteamento por interface
- **`launcher.py`** — usa `systemd-run --scope -pBindInterfaces=<iface>` quando disponível
- Fallback para `scripts/launch_on_iface.sh` (network namespace via `ip netns` / `unshare`)
- `scripts/setup-network.sh` — configura permissões uma única vez via `pkexec`

### Monitoramento de processos
- **`process_monitor.py`** — scan via `psutil`, match por padrão de nome/cmdline, detecção de melhor PID por app
- **`process_cache.py`** — cache com refresh assíncrono e debounce
- Aba **Processos ativos** — tabela com PID, proxy, IP na web, rede, status, comando
- Ações inline: ativar/remover proxy, trocar interface

### Verificação e saúde
- **`proxy_health.py`** — checa IP público via proxy e direto, geo lookup (`ip-api.com`), comparação IP proxy ≠ IP direto
- Verificação de cadeia via `curl` antes de declarar proxy ativo
- Display de IP + bandeira do país no header

### GUI (customtkinter)
- Tema escuro, janela 1100×720
- **Header** — interruptor global PROXY LIGADO/DESLIGADO, tiles de modo (⚡/🧅), seletor de país, IPs direto + proxy com bandeiras
- **Barra de acesso rápido** — tiles clicáveis dos apps destaque; clique direito para ir à configuração
- **Aba Aplicativos** — lista com seções (proxy ativo, recentes, por categoria), filtro de busca, toggle por app, selector de rede visual, menu de ações
- **Aba Processos ativos** — tabela com todas as colunas, ações em lote
- **Aba Configurações** — painéis por fonte (custom, gratuito, pago, tor), NO_PROXY, CA extra, auto-config, botões de teste/salvar

---

## O que falta — por prioridade

### P0 — Robustez crítica

| Item | Status | Por quê |
|------|--------|---------|
| **Watchdog do proxy local** | ✅ Implementado | Thread daemon (12s) em `local_proxy.py`; reinicia gost automaticamente, notifica GUI e desktop. |
| **Refresh de interfaces de rede** | ✅ Implementado | `_refresh_iface_choices()` chamado a cada 30s; reconstrói tiles dos cards ao detectar mudança. |
| **Upgrade para gost v3** | ✅ Implementado | `local_proxy.py` atualizado para go-gost v3.0.0 (.tar.gz); `direct` sem flag `-F`. |
| **Cleanup ao fechar com crash** | ✅ Implementado | `main.py` com `atexit.register` + `signal.signal(SIGTERM/SIGINT)` chamando `stop_watchdog` + `stop_local_proxy`. |
| **Testes automatizados** | ✅ Implementado | `tests/test_basic.py` com 20 testes (models, proxy_env, network, config + perfis). 20/20 passando. |

### P1 — Funcionalidades importantes

| Item | Status | Por quê |
|------|--------|---------|
| **Múltiplos perfis de proxy** | ✅ Implementado | `profiles.py` + `ConfigStore.save_profile/load_profile/delete_profile`; painel na aba Configurações. |
| **Notificação de desktop** | ✅ Implementado | `notifications.py` via `notify-send`; disparado em proxy up/down/error/recovered. |
| **Ícone na bandeja do sistema** | ✅ Implementado | `tray.py` via `pystray` (graceful fallback se não instalado); atualiza cor com estado do proxy. |
| **Tor por país (ligar a flag)** | ✅ Implementado | `TOR_EXIT_COUNTRY_ENABLED = True` em `tor_country.py`. |
| **Pool de proxies / rotação** | ✅ Implementado | Watchdog chama `restart_local_proxy` ao detectar queda; auto-config refaz busca se necessário. |
| **Proxy diferente por app** | ✅ Implementado | Campo `upstream_proxy` em `AppRule`; `build_proxy_env` usa URL direta quando definido; campo no diálogo Editar. |
| **Autostart na sessão** | ⬜ Pendente | Gerar entrada `.desktop` em `~/.config/autostart/` ou unit systemd user. |

### P2 — UX e qualidade

| Item | Por quê |
|------|---------|
| **Descoberta automática de apps instalados** | Hoje o catálogo é fixo. Escanear `/usr/bin`, `.desktop` files e flatpaks para sugerir apps instalados. |
| **Drag-and-drop na lista de apps** | Reordenar apps manualmente (hoje a ordem é automática por status/recentes/categoria). |
| **Tema claro / escuro por escolha** | Forçado dark. Respeitar preferência do sistema ou deixar o usuário escolher. |
| **Personalizar barra de acesso rápido** | `FEATURED_APP_IDS` é hardcoded. Deixar o usuário escolher quais apps aparecem. |
| **Import / Export de config** | Backup e compartilhamento do `config.json` via diálogo de arquivo. |
| **Wizard de primeiro uso** | Guiar o usuário novo: escolha de fonte → teste → primeiro app. Evita confusão com a aba Configurações. |
| **Histórico de proxy** | Log de quando proxy ligou/desligou e qual IP foi usado. Útil para auditoria. |

### P3 — Expansão de plataforma

| Item | Por quê |
|------|---------|
| **Múltiplos perfis Firefox** | Hoje gerencia só o perfil padrão. Suporte a múltiplos perfis e ao modo container. |
| **Brave / Edge / Chromium** | Mesmo mecanismo do Chrome (`--proxy-server`), mas requer detecção correta do executável. |
| **Electron apps genéricos** | Discord, Slack, VS Code usam Chromium interno. Injetar `--proxy-server` neles também. |
| **IPv6** | Toda detecção de IP e roteamento é IPv4. Conexões dual-stack podem vazar. |
| **DNS leak prevention** | Proxy via env var não intercepta DNS. Considerar redirecionar DNS para resolver via proxy (DoH ou socks5h). |
| **SOCKS5 com autenticação** | `proxy_env.py` suporta no campo, mas o formato `socks5h://user:pass@host:port` precisa ser validado no gost. |
| **macOS (futuro)** | Depende de `/proc`, `psutil` Linux-específico, `systemd-run`. Abstrair para suportar macOS: `launchctl`, `networksetup`, `dscl`. |

### P4 — Segurança e privacidade

| Item | Por quê |
|------|---------|
| **Armazenamento seguro de credenciais** | Usuário/senha do proxy ficam em `config.json` em texto plano. Usar `libsecret` / `keyring` do sistema. |
| **MITM cert management** | Para inspecionar HTTPS via proxy local, precisaria gerar CA + instalar no sistema/browsers. Fora do escopo atual, mas relevante para debugging. |
| **Auditoria de vazamento** | Botão "testar vazamento" que verifica se DNS, WebRTC e IP público coincidem com o esperado. |
| **Verificação de integridade do gost** | Download do binário sem verificação de hash/assinatura. Risco de supply chain se o GitHub for comprometido. |

---

## Débito técnico

| Item | Arquivo | Problema |
|------|---------|----------|
| `_SYSTEMD_BIND` verificado no import | `launcher.py:33` | Executado em todo import, mesmo que nunca seja usado. Mover para lazy init. |
| Migrações de config em cadeia | `config.py:101-134` | `if from_version < N` aninhado fica frágil. Migrar para dict de funções. |
| `gost` v2 hardcoded | `local_proxy.py:24-27` | Versão e URL fixas no código. Mover para constante de configuração ou arquivo de versão. |
| `CTkFont` recriado por card | `gui.py` | Fontes são criadas no `__init__` mas algumas instâncias ainda criam no render. Consolidar. |
| `scan_processes` sem timeout | `process_monitor.py:39` | `psutil.process_iter` sem timeout pode bloquear se um processo parar de responder. |
| Free proxy URLs hardcoded | `proxy_sources.py:80-86` | Se um repositório mudar ou sair do ar, a busca silencia. Externalizar para config ou URL atualizada via CDN. |
| `detect_process_interface` lento | `network.py:117` | Iteração sobre todas as conexões do processo. Pode ser lento para processos com muitas conexões (browsers). |
| Config version bump manual | `config.py:13` | `CONFIG_VERSION = 10` — fácil esquecer de incrementar ao adicionar campo. Considerar hash do schema ou geração automática. |

---

## Arquitetura sugerida para próximas versões

```
proxy_manager/
  core/
    models.py          ← sem mudanças
    config.py          ← adicionar perfis múltiplos
    migrator.py        ← extrair migrações do config.py
  proxy/
    local_proxy.py     ← abstrair backend (gost v3, pproxy, mihomo)
    proxy_env.py       ← sem mudanças
    proxy_sources.py   ← adicionar pool + rotação
    proxy_health.py    ← adicionar DNS leak check
  apps/
    presets.py         ← adicionar descoberta automática
    launcher.py        ← abstrair OS (Linux / macOS)
    process_monitor.py ← adicionar timeout + watchdog
    browser_proxy.py   ← adicionar Brave/Edge/Electron
    claude_proxy.py    ← sem mudanças
  network/
    network.py         ← adicionar refresh dinâmico
    tor_country.py     ← ligar TOR_EXIT_COUNTRY_ENABLED
  ui/
    gui.py             ← dividir em módulos por aba
    tray.py            ← novo: ícone de bandeja
    wizard.py          ← novo: onboarding
    notifications.py   ← novo: libnotify
  scripts/
    install.sh
    setup-network.sh
    launch_on_iface.sh
```

---

_Última atualização: 2026-06-28_
