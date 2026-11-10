# MineCortes 🌴

Cortes de live com legenda, zoom, postagem e **agendamento** para YouTube, TikTok e Instagram.

## Instalar (Windows)
1. Instale o **Python 3.10+** (marque "Add Python to PATH"; o instalador já traz a janela/tkinter).
2. Instale o ffmpeg: `winget install ffmpeg` (reabra o terminal depois).
3. Na pasta do app: `pip install -r requirements.txt`
4. Abra com duplo clique em **MineCortes.bat** (ou `python minecortes_gui.py`).

## Como usar (as abas)
1. **Vídeo**: escolha a live, quantos cortes e a duração. Clique em *Analisar*.
2. **Cortes**: marque/desmarque, edite início, fim e título, adicione corte manual, clique em *Gerar*. Embaixo, *Vídeo longo* junta os melhores momentos na duração que você quiser.
3. **Legendas**: 6 estilos prontos e controle total (fonte, tamanho, cores, contorno, sombra, caixa, animação, altura, modo palavra a palavra). A **prévia muda ao vivo**. Dá para salvar seu estilo.
4. **Publicar e agendar**: escolha as plataformas, hashtags, e *Assim que ficar pronto* ou *Agendar* (data, hora e intervalo entre cortes). Acompanhe tudo na fila.
5. **Automático**: vigia uma pasta, corta cada live nova e agenda os posts espaçados.
6. **Contas**: chaves do YouTube, TikTok e Instagram.

Sem janela: `python minecortes.py clips live.mp4`, `compile`, `watch`, `fila`.

## O que dá (e o que não dá) para agendar

| Plataforma | Agendamento | Precisa deixar o programa aberto? |
|---|---|---|
| **YouTube** | **Nativo.** O vídeo sobe privado com horário (`publishAt`) e o YouTube publica sozinho. | **Não** |
| **TikTok** | A API só posta na hora. O MineCortes guarda o corte e posta no horário. | **Sim** (PC ligado e programa aberto) |
| **Instagram** | A API de publicação não tem agendamento. O MineCortes posta no horário. | **Sim** (PC ligado e programa aberto) |

Se o programa estiver fechado na hora marcada (TikTok/Instagram), e passar de 30 min, o item fica como **⚠ perdido** em vez de postar sem você saber. Use *Postar agora* na fila. O Instagram limita a 50 posts por 24 h pela API.

## Contas: como conectar

**YouTube**
1. console.cloud.google.com → novo projeto → ative **YouTube Data API v3**.
2. Credenciais → *ID do cliente OAuth* → tipo **App para computador** → baixe o JSON.
3. Aba *Contas*: escolha o JSON e clique *Conectar*. Autorize no navegador (1 vez).
4. Pode ser que o Google deixe os vídeos de projetos não verificados só como privados até uma auditoria. Teste com um corte antes de depender disso.

**TikTok**
1. developers.tiktok.com → crie um app com **Content Posting API** (Direct Post).
2. Gere o access token da sua conta e cole na aba *Contas*. O token expira e precisa ser renovado.
3. Apps sem auditoria costumam só poder postar como privado (*SELF_ONLY*).

**Instagram**
1. Conta **profissional** (Criador ou Comercial).
2. App no developers.facebook.com com permissão de publicação de conteúdo. Cole o **ID da conta** e o **token** na aba *Contas*.
3. O envio é pelo arquivo local (upload resumível), sem precisar de URL pública. A versão da API (ex.: `v23.0`) é editável na aba, caso a Meta aposente a atual.

> Não consegui testar os envios reais aqui (preciso das suas contas). A geração de cortes, legendas, prévia, fila e agendamento foram testadas; os uploads seguem a documentação das plataformas e podem pedir ajustes no primeiro uso. Comece com visibilidade **privada**.

## Limites honestos
- Os "melhores momentos" vêm de volume da voz, ritmo de fala e palavras de reação. Não entende o jogo: revise antes de tornar público.
- O zoom é no centro do quadro. Para lives de jogo, o layout *fundo desfocado* preserva a tela inteira.
- Dados das contas ficam em `minecortes_config.json` neste PC. Não compartilhe essa pasta.
- Use só conteúdo seu ou com permissão.
