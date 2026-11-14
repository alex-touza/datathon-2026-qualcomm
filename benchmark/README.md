# Proves de M3Kang amb Qwen i models de decisió

Executeu les ordres des de l'arrel del worktree. Els pesos, les dades de treball, la clau i els resultats detallats són locals i Git els ignora sota `data/`. El servidor Qwen escolta a loopback.

## Mostra i servidor

El pilot utilitza el primer shard `high_quality` de M3Kang, fixat per revisió i SHA-256. Cal un TSV privat de revisió amb una fila per problema: ID, tipus (`text`, `figure` o `graphic`), tres SHA-256 d'imatge separats amb `|` en ordre eng/cat/esp i cinc opcions textuals separades amb `|` quan pertoqui. Vegeu el TSV que acompanya les dades locals. Per regenerar el pilot, instal·leu els paquets de `laptop/requirements.txt` al vostre entorn i executeu:

```sh
python benchmark/prepare.py --reviewed data/benchmark/pilot.tsv --out data/benchmark
```

El projector correspon a la mateixa revisió que el Qwen de `config.env`. Els SHA-256 dels pesos i del projector es poden comprovar amb `shasum -a 256`.

```sh
llama-server --model "$HOME/models/Qwen_Qwen3.5-0.8B-Q8_0.gguf" \
  --mmproj data/benchmark/runtime/mmproj.gguf --image-max-tokens 128 \
  --host 127.0.0.1 --port 18083 --threads 4 --ctx-size 4096 --parallel 1 \
  --cache-ram 0 --ctx-checkpoints 2 --jinja --reasoning off --reasoning-budget 0 \
  --alias qwen3.5-0.8b
```

## Tres passades locals

```sh
for arm in ocr reviewed vision; do
  backend=llama-text
  option_source="$arm"
  if [ "$arm" = vision ]; then backend=llama-vision; option_source=reviewed; fi
  python3 benchmark/run.py --input data/benchmark/pilot.jsonl \
    --backend "$backend" --options "$option_source" --model qwen3.5-0.8b \
    --endpoint http://127.0.0.1:18083 --out "data/benchmark/results/qwen-$arm.jsonl"
done
```

Els JSONL registren predicció, durada, ús i temps del servidor si estan disponibles. El mateix intent puntua a 60 i 100 segons, sobre el total de files seleccionades. Una resposta correcta posterior al llindar no compta en aquell llindar. Amb `--language`, `--kind` i `--id` es poden seleccionar submostres. Les opcions gràfiques necessiten la imatge de la pregunta per poder-se llegir.

## Jev allotjat

La llicència de M3Kang restringeix la divulgació del dataset a tercers. La comparació allotjada utilitza problemes originals generats al repositori. Per comparar en igualtat d'entrada, executeu Qwen també sobre aquest manifest.

```sh
python benchmark/synthetic.py --out data/benchmark/synthetic
mkdir -p data/benchmark-private
chmod 700 data/benchmark-private
(umask 077; set -C; printf 'TYPESAFE_API_KEY=\n' > data/benchmark-private/jev.env)
```

Escriviu la clau després de `TYPESAFE_API_KEY=` amb un editor local. Si ja teniu el fitxer protegit, passeu-ne el camí directament amb `--key-file`.

```sh
for arm in text vision; do
  python3 benchmark/run.py --input data/benchmark/synthetic/pilot.jsonl \
    --backend "typesafe-$arm" --options reviewed --model jev-1.14.0 \
    --key-file data/benchmark-private/jev.env \
    --out "data/benchmark/results/original-jev-$arm.jsonl"
done
```

El mode de visió processa en text les files sense imatge. Compareu les files amb figura per mesurar l'efecte de l'entrada visual.

## Resum de la primera tanda

Qwen3.5 0.8B Q8_0 amb projector F16 i llama.cpp `b1b770d90` al Mac, sense límit de recursos d'UNO Q.

| Mode | Encerts ≤60 s | Encerts ≤100 s | Mediana al Mac | P95 al Mac |
| --- | ---: | ---: | ---: | ---: |
| OCR | 13/72 | 13/72 | 0,163 s | 0,314 s |
| Opcions revisades | 13/72 | 13/72 | 0,162 s | 0,293 s |
| Opcions revisades i imatge | 16/72 | 16/72 | 0,421 s | 0,551 s |

La mostra inclou 24 problemes únics, en tres idiomes i vuit nivells. Les traduccions comparteixen etiqueta i les files no són independents. Sempre respondre C aconsegueix 24/72 en aquesta mostra. La passada de visió envia la captura revisada completa. La durada és local al Mac i cal una nova mesura a la placa per validar els terminis del concurs.

```sh
python3 benchmark/summarize.py data/benchmark/results/qwen-{ocr,reviewed,vision}.jsonl
```

## Liquid d1-omni-600M local

La passada de decisió local utilitza el model de [Liquid AI](https://huggingface.co/LiquidAI/d1-omni-600M)
amb la revisió `02b55d7076f15129e59ab3f94783f32c4b088674`. Cal preparar un
entorn aïllat amb PyTorch, Transformers 5.15 o posterior, Pillow i safetensors.
Descarregueu el snapshot d'aquesta revisió en un directori local i
passeu-lo amb `--model`. Transformers carrega el codi del snapshot local
amb `trust_remote_code=True`.

```sh
python benchmark/omni.py --input data/benchmark/pilot.jsonl --mode vision \
  --model ../omni-runtime/model --out data/benchmark/results/omni-vision.jsonl
```

Amb `--mode text` el model llegeix l'enunciat i les opcions. El mode
`vision` afegeix les imatges disponibles. La càrrega inicial del model
queda fora de la latència per problema.
