# Proxy Manager

GUI para ligar/desligar proxy por aplicativo no Linux — sem instalar Clash ou v2ray manualmente.

## O que o app faz

1. **Interruptor PROXY LIGADO / DESLIGADO** — inicia um proxy local em `127.0.0.1:7890` (via [gost](https://github.com/ginuerzh/gost)) que encaminha para o seu servidor externo.
2. **Por app** — **Ligar proxy** / **Parar proxy** reinicia só aquele programa com ou sem `HTTP_PROXY`.
3. **Monitor** — vê quais processos estão rodando com proxy ativo.

Você só precisa configurar **uma vez** o proxy externo (host, porta, usuário/senha) na aba Configurações.

## Requisitos

- Python 3.10+
- Linux (usa `/proc` para ler variáveis de ambiente dos processos)
- Conexão com a internet na primeira instalação (baixa o binário `gost`)

## Instalação (uma vez)

```bash
cd proxy_generic
bash scripts/install.sh
```

Isso cria o ambiente Python, instala dependências e baixa o `gost` em `~/.local/share/proxy-manager/bin/gost`.

## Executar

```bash
./run.sh
```

## Uso rápido

1. Abra **Configurações** e escolha a **fonte** do proxy:
   - **Personalizado** — host/porta manual
   - **Gratuito** — busca listas públicas (botão *Buscar proxies*)
   - **Pago** — templates Smartproxy, Bright Data, Oxylabs, Webshare, etc.
   - **Tor** — SOCKS5 via serviço Tor local (porta 9050)
2. Ligue o interruptor **PROXY LIGADO** no topo.
3. Na aba **Aplicativos**, clique em **Ligar proxy** no programa desejado.

Para desligar: **Parar proxy** no app e/ou desligue o interruptor global.

## Roteamento por interface (opcional)

Para forçar um app a usar Ethernet ou Wi-Fi:

```bash
sudo scripts/setup-network.sh
```

Na primeira vez pode pedir senha via `pkexec`.

## Observações

- Reiniciar com proxy **encerra e relança** o processo com novas variáveis de ambiente.
- Apps abertos fora do gerenciador não são alterados automaticamente.
- Config em `~/.config/proxy-manager/config.json`; log do gost em `~/.config/proxy-manager/gost.log`.

## Estrutura

```
proxy_manager/
  local_proxy.py     # gost local 127.0.0.1:7890 → upstream
  config.py          # persistência JSON
  proxy_env.py       # HTTP_PROXY
  process_monitor.py # scan de processos
  gui.py             # interface
scripts/install.sh   # instalação completa
main.py
```
