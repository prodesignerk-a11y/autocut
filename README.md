# AutoCut ✂️ — Edição Inteligente de Vídeo

Remove pausas, silêncios e conversas paralelas automaticamente com IA.

---

## Pré-requisitos

### 1. FFmpeg (obrigatório)

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt update && sudo apt install ffmpeg
```

**Windows:**
Baixe em https://ffmpeg.org/download.html e adicione ao PATH.

---

### 2. Python 3.10+

```bash
python --version  # deve ser 3.10 ou superior
```

---

## Instalação

```bash
# Clone ou extraia o projeto
cd autocut

# Crie um ambiente virtual (recomendado)
python -m venv venv
source venv/bin/activate       # Linux/macOS
# venv\Scripts\activate        # Windows

# Instale as dependências
pip install -r backend/requirements.txt
```

> ⚠️ A instalação do PyTorch (Silero VAD) pode demorar alguns minutos.
> O Whisper baixa o modelo (~140MB) na primeira execução.

---

## Uso via Interface Web

### 1. Iniciar o backend

```bash
cd backend
uvicorn main:app --reload --port 8000
```

O servidor sobe em: http://localhost:8000

### 2. Abrir o frontend

Abra o arquivo `frontend/index.html` diretamente no navegador:

```bash
open frontend/index.html       # macOS
xdg-open frontend/index.html   # Linux
start frontend/index.html      # Windows
```

### 3. Processar

1. Arraste ou selecione um vídeo (MP4, MOV, MKV)
2. Escolha o modo de corte:
   - 🌿 **Leve** — remove silêncios > 700ms
   - ⚡ **Médio** — remove silêncios > 400ms (padrão)
   - 🔥 **Agressivo** — remove silêncios > 200ms
3. Clique em **Processar Vídeo**
4. Aguarde e baixe o resultado

---

## Uso via Linha de Comando (CLI)

```bash
# Processamento básico (modo médio)
python autocut_cli.py meu_podcast.mp4

# Modo agressivo para Reels/TikTok
python autocut_cli.py entrevista.mp4 --mode aggressive

# Modo leve preservando respirações
python autocut_cli.py aula.mov --mode light --output aula_editada.mp4

# Personalizar threshold manualmente
python autocut_cli.py video.mp4 --silence-ms 350 --padding 80

# Sem filtro de ruído de fundo
python autocut_cli.py live.mkv --no-bg-filter
```

### Opções CLI

| Opção | Descrição | Padrão |
|-------|-----------|--------|
| `--mode` | `aggressive` / `medium` / `light` | `medium` |
| `--silence-ms` | Threshold customizado em ms | — |
| `--padding` | Margem em ms antes/depois de cada fala | `50` |
| `--output` / `-o` | Caminho do arquivo de saída | `*_autocut.mp4` |
| `--no-bg-filter` | Desativa filtro de ruído de fundo | — |

---

## API REST

O backend expõe uma API REST completa:

| Endpoint | Método | Descrição |
|----------|--------|-----------|
| `/api/upload` | POST | Upload do vídeo |
| `/api/process/{id}` | POST | Iniciar processamento |
| `/api/status/{id}` | GET | Consultar progresso |
| `/api/download/{id}` | GET | Baixar vídeo editado |
| `/api/job/{id}` | DELETE | Limpar arquivos |

Documentação interativa (Swagger): http://localhost:8000/docs

---

## Pipeline de Processamento

```
Vídeo Input
    │
    ▼
[1] Extração de áudio (FFmpeg → WAV 16kHz mono)
    │
    ▼
[2] Detecção de fala (Silero VAD)
    │   └─ Fallback: pydub energy detection
    │
    ▼
[3] Refinamento com Whisper (word-level timestamps)
    │   └─ Opcional, melhora precisão dos cortes
    │
    ▼
[4] Aplicação de padding + merge de segmentos próximos
    │
    ▼
[5] Renderização final (FFmpeg concat filter)
    │   └─ H264 + AAC, sem re-encode desnecessário
    │
    ▼
Vídeo Output (MP4 H264)
```

---

## Casos de Uso

| Tipo de Conteúdo | Modo Recomendado |
|-----------------|-----------------|
| Reels / TikTok / Shorts | 🔥 Agressivo |
| Entrevistas / Podcasts | ⚡ Médio |
| Aulas / Tutoriais | 🌿 Leve |
| Conferências / Palestras | ⚡ Médio |

---

## Estrutura do Projeto

```
autocut/
├── backend/
│   ├── main.py           ← API FastAPI
│   ├── processor.py      ← Pipeline de processamento
│   └── requirements.txt  ← Dependências Python
├── frontend/
│   └── index.html        ← Interface web completa
├── autocut_cli.py        ← CLI de linha de comando
├── uploads/              ← Vídeos recebidos (criado auto)
├── outputs/              ← Vídeos processados (criado auto)
└── README.md
```

---

## Troubleshooting

**Erro: "ffmpeg not found"**
→ Instale o FFmpeg conforme instruções acima e reinicie o terminal.

**Erro: "No speech detected"**
→ Verifique se o áudio do vídeo está funcional. Tente com `--no-bg-filter`.

**Whisper muito lento**
→ Normal na primeira execução (baixa modelo). Use GPU com `torch.cuda` para acelerar.

**Erro de CORS no browser**
→ Certifique que o backend está rodando em `localhost:8000`.

---

## Tecnologias

- **Silero VAD** — Detecção de fala local, rápida e precisa
- **OpenAI Whisper** — Transcrição com timestamps por palavra
- **FFmpeg** — Extração de áudio e renderização de vídeo
- **FastAPI** — API assíncrona de alta performance
- **pydub** — Fallback de detecção por energia de áudio
