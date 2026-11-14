# Proves de M3Kang amb Qwen i models de decisió

Executeu les ordres des de l'arrel del worktree. Els pesos, les dades de treball, la clau i els resultats detallats són locals i Git els ignora sota `data/`. El servidor Qwen escolta a loopback.

## Mostra i servidor

El pilot utilitza el primer shard `high_quality` de M3Kang, fixat per revisió i SHA-256. Cal un TSV privat de revisió amb una fila per problema: ID, tipus (`text`, `figure` o `graphic`), tres SHA-256 d'imatge separats amb `|` en ordre eng/cat/esp i cinc opcions textuals separades amb `|` quan pertoqui. Vegeu el TSV que acompanya les dades locals. Per regenerar el pilot, instal·leu els paquets de `laptop/requirements.txt` al vostre entorn i executeu:

```sh
python benchmark/prepare.py --reviewed data/benchmark/pilot.tsv --out data/benchmark
```

El projector correspon a la mateixa revisió que el Qwen de `config.env`.
Per descarregar-lo i verificar-lo:

```sh
mkdir -p data/benchmark/runtime
curl -fL -o data/benchmark/runtime/mmproj.gguf \
  https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/resolve/f36b1ea49a332ede8fe5f389bbf5b3575ef71f48/mmproj-Qwen_Qwen3.5-0.8B-f16.gguf
printf '%s  %s\n' 1dc1351c82e41b48edb55fd6ddfa7ca60fb5a16b3d5abf3ce7054880dd022847 \
  data/benchmark/runtime/mmproj.gguf | shasum -a 256 -c
```

Els pesos de Qwen es poden obtenir amb `board/03_download_models.sh`.

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
| Liquid d1-omni-600M amb imatge (CPU) | 10/72 | 10/72 | 3,851 s | 7,053 s |

La mostra inclou 24 problemes únics, en tres idiomes i vuit nivells. Les traduccions comparteixen etiqueta i les files no són independents. Sempre respondre C aconsegueix 24/72 en aquesta mostra. Les passades de visió envien la captura revisada completa. Qwen guanya 8 respostes correctes i en perd 5 quan rep la imatge amb les mateixes opcions revisades. La durada és local al Mac i cal una nova mesura a la placa per validar els terminis del concurs.

```sh
python3 benchmark/summarize.py data/benchmark/results/qwen-{ocr,reviewed,vision}.jsonl \
  data/benchmark/results/omni-pilot-vision.jsonl
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

## Comparació amb Jev sobre problemes originals

Cada prova consumeix el mateix manifest. La clau és la parella `(id, language)`.
Les files amb la mateixa `id` són traduccions del mateix problema. Un model de
visió rep el PNG només quan el manifest en proporciona un.

- Qwen rep un missatge de sistema, l'enunciat i les opcions. En visió, el missatge
  també conté la imatge. `llama-server` restringeix la resposta amb la gramàtica
  `root ::= [A-E]` i un únic token de sortida.
- Jev allotjat rep l'enunciat a `state`, les alternatives com a criteris d'una
  pregunta `Choice` i la imatge com a asset, si n'hi ha. El runner llegeix
  `answers.answer.choice`.
- d1-omni local usa la pregunta tipada equivalent i passa el PNG com a objecte RGB.
  El runner també llegeix `choice` del resultat estructurat.
- Els runners reben l'etiqueta per puntuar un cop feta la inferència. Els originals
  inclouen figures generades a partir d'una solució calculada i M3Kang incorpora
  la captura revisada de cada versió. Enviem una sola imatge completa per pregunta.
  El projector de Qwen controla el pressupost d'imatge a `llama-server`.

| Model i modalitat | Encerts ≤60 s | Encerts ≤100 s | Mediana | P95 |
| --- | ---: | ---: | ---: | ---: |
| Jev 1.14 text | 30/36 | 30/36 | 0,254 s | 0,316 s |
| Jev 1.14 visió | 36/36 | 36/36 | 0,256 s | 0,337 s |
| Qwen 0.8B text | 10/36 | 10/36 | 0,099 s | 0,257 s |
| Qwen 0.8B visió | 13/36 | 13/36 | 0,232 s | 0,311 s |
| d1-omni-600M text | 4/36 | 4/36 | 0,086 s | 0,179 s |
| d1-omni-600M visió | 5/36 | 5/36 | 0,454 s | 1,666 s |

Els 36 casos representen 12 problemes originals. El generador inicial posava la
resposta correcta a la mediana numèrica de les cinc opcions. La regla de triar
la mediana encerta 36/36 sense rebre enunciat ni imatge. El resultat inicial
serveix per verificar el flux d’inferència. La mostra ampliada varia el rang
numèric de la resposta. La latència de Jev inclou xarxa
i servei. Els temps de Qwen i d1-omni corresponen al Mac amb el model carregat.

El control amb Jev i només les opcions del pilot inicial va obtenir 13/36.

## Mostra ampliada de M3Kang

`stratified.py` busca a les quatre parts de `high_quality` i les deu de
`standard` del snapshot fixat. Dona prioritat a `high_quality` quan el mateix
problema i idioma apareixen als dos splits. Selecciona candidats per nivell i
modalitat amb llavor fixa, excloent els identificadors del primer pilot.

```sh
python benchmark/stratified.py stage --exclude data/benchmark/pilot.jsonl \
  --per-kind 2 --out data/benchmark/stratified-stage
python benchmark/stratified.py export --staging data/benchmark/stratified-stage \
  --reviewed CAMI_AL_TSV_PRIVAT --out data/benchmark/stratified
```

El TSV privat de revisió conté slot, rang, tipus, SHA-256 del PNG i, per a
opcions textuals, cinc opcions separades per `|`. L'export verifica la cobertura
de tots els slots i el hash de cada imatge revisada. La mostra d'aquest
benchmark conté 32 problemes nous, dos només amb text i dos amb imatge per
nivell, amb 11 entrades en anglès, 11 en català i 10 en castellà. En aquest
sorteig totes les files seleccionades són de `high_quality`.

| Mode sobre la mateixa mostra M3Kang | Encerts ≤60 s | Encerts ≤100 s | Mediana al Mac |
| --- | ---: | ---: | ---: |
| Qwen 0.8B · OCR | 3/32 | 3/32 | 0,096 s |
| Qwen 0.8B · opcions revisades | 6/32 | 6/32 | 0,101 s |
| Qwen 0.8B · opcions revisades + captura | 7/32 | 7/32 | 0,223 s |
| d1-omni-600M · opcions revisades + captura | 7/32 | 7/32 | 1,680 s |

## Originals ampliats

```sh
python benchmark/synthetic.py --expanded --out data/benchmark/synthetic-expanded
```

Això genera els 36 casos inicials i 96 files noves amb identificadors
`expanded-*`. Per reproduir la comparació ampliada, filtreu el manifest a
aquests identificadors i mantingueu les rutes de les imatges relatives al
directori del manifest. Hi ha dos problemes de text i dos de gràfic per
cadascuna de les vuit bandes construïdes, amb tres traduccions de cada
problema. Les bandes dels originals no tenen una calibració de dificultat.
Les figures són PNG de 800 × 500. Els distractors es mostregen d'un interval
de magnitud sense un patró fix de distàncies a la solució, però poden ser poc
plausibles en alguns enunciats.
