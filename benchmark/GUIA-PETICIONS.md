# De la fila del benchmark a la resposta del model

El programa llegeix un fitxer JSONL, construeix una petició per a cada parella `(id, language)` i desa la lletra retornada. Després compara la lletra amb `label`, la resposta de referència. Per puntuar a 60 i 100 segons s'aplica cada llindar al temps del mateix intent.

## D'on provenen les files

| Mostra de les proves anteriors | Problemes únics | Files | Origen i selecció |
| --- | ---: | ---: | --- |
| Primer pilot M3Kang | 24 | 72 | 8 de text, 8 amb figura i opcions textuals i 8 amb opcions gràfiques. Cada problema té una fila en anglès, català i castellà. |
| Pilot M3Kang estratificat | 32 | 32 | 2 de text i 2 que necessiten la imatge a cadascun dels nivells 0–7. Un idioma per problema, amb 11 files en anglès, 11 en català i 10 en castellà. Els identificadors són diferents dels del primer pilot. |
| Originals inicials | 12 | 36 | Problemes creats amb `synthetic.py`, en tres idiomes. La resposta correcta era sempre la mediana numèrica de les opcions. |
| Originals ampliats | 32 | 96 | 2 de text i 2 amb gràfic per cadascuna de les vuit bandes construïdes, en tres idiomes. El contrast ampliat utilitza aquestes 96 files. El generador també conserva les 36 inicials al manifest complet. |

M3Kang aporta l'enunciat, la captura, l'identificador, l'idioma, el nivell oficial i l'etiqueta de referència. La selecció estratificada va buscar candidats a les 14 parts del snapshot local i va donar prioritat a `high_quality` quan es repetien el problema i l'idioma. Les 32 files escollides van sortir de `high_quality`. Els originals generen l'enunciat, les opcions, la solució i, si cal, la figura. Les seves bandes són una manera de repartir els casos i no tenen calibració de dificultat M3Kang. [Fitxa de M3Kang](https://huggingface.co/datasets/qualcomm/M3Kang)

Al snapshot complet, els dos splits sumen 149.294 files. Hi ha 108.677 parelles diferents `(id, language)`, perquè 40.617 es repeteixen als dos splits. Si agrupem totes les traduccions pel seu `id`, queden 1.747 problemes, 811 de text i 936 que necessiten la imatge. [Consulta SQL de la distribució](distribution.sql) · [Codi que l’executa](distribution.py)

Les dades M3Kang s'han processat localment amb Qwen i Omni. El runner d'HTTP de Jev només accepta files amb `dataset_origin: "synthetic"` per respectar la restricció de divulgació a tercers de M3Kang. [Runner](run.py) · [Llicència de M3Kang](https://huggingface.co/datasets/qualcomm/M3Kang/blob/main/LICENSE.pdf)

## Què conté una fila

- `id`, `language` i `level` identifiquen la versió i permeten agrupar els resultats. `kind` indica `text`, `figure` o `graphic`. `text_only` indica si el problema és resoluble amb l'enunciat i les alternatives textuals.
- `text` és l'enunciat. `image` és un camí relatiu al manifest o `null`. Una fila M3Kang de tipus `text` també conserva la captura de la pregunta.
- `options` conté les alternatives extretes de la captura per OCR en M3Kang. L'OCR es fa amb RapidOCR en preparar la mostra. `reviewed_options` conté la transcripció revisada. El runner llegeix un dels dos camps amb `--options ocr` o `--options reviewed` i passa els valors disponibles al model. Si les alternatives són figures, el diccionari revisat és buit i les figures es veuen a la captura. En els originals, el generador escriu directament les alternatives als dos camps.
- `dataset_origin` indica la procedència. `label` es llegeix després de la inferència per calcular `correct`. L'etiqueta i les metadades d'avaluació del manifest queden fora de la petició. [Preparació del pilot](prepare.py) · [Mostra estratificada](stratified.py) · [Generador original](synthetic.py)

## Qwen local

Qwen3.5 0.8B Q8_0 s'executa amb `llama-server` al Mac. Aquest exemple mostra el cos JSON de `POST http://127.0.0.1:18083/v1/chat/completions`. El problema dels triangles és inventat per a aquesta guia. Substitueix `BASE64_DEL_PNG` pels bytes del seu PNG en base64. El marcador manté el JSON vàlid, però el cos només es pot enviar després de substituir-lo per una imatge real.

```json
{
  "model": "qwen3.5-0.8b",
  "messages": [
    {
      "role": "system",
      "content": "You solve multiple-choice math competition problems for school students. Read the problem and the options, then reply with the letter of the correct option."
    },
    {
      "role": "user",
      "content": [
        {
          "type": "text",
          "text": "Solve the multiple-choice math problem. Return only one capital letter: A, B, C, D, or E.\nDo not include explanation or punctuation.\n\nQuants triangles blaus hi ha a la imatge?\n\nAnswer choices:\nA. 1\nB. 2\nC. 3\nD. 4\nE. 5"
        },
        {
          "type": "image_url",
          "image_url": {"url": "data:image/png;base64,BASE64_DEL_PNG"}
        }
      ]
    }
  ],
  "temperature": 0,
  "seed": 42,
  "max_tokens": 1,
  "grammar": "root ::= [A-E]",
  "cache_prompt": true,
  "stream": false
}
```

En mode text, `messages[1].content` és només la cadena que hi ha al bloc `text`. En mode visió, el fitxer complet s'adjunta al mateix missatge com a URL de dades i el projector F16 el converteix en entrada visual. El servidor de les proves tenia `--image-max-tokens 128` i `--ctx-size 4096`. 128 és el pressupost visual configurat. El recompte total que informa el servidor pot ser més alt perquè inclou el text i els tokens de format. Algunes captures també poden generar més tokens visuals segons com les processa el projector. `max_tokens: 1` limita la sortida i la gramàtica la restringeix a A–E. El runner llegeix `choices[0].message.content`. [Runner](run.py) · [Documentació de llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

## Jev allotjat

Jev 1.14 s'executa al servei de TypeSafe. El runner fa un `POST https://api.typesafe.ai/v1/systemone` amb `Content-Type: application/json`, l'autenticació Bearer i, quan adjunta la imatge, `TypeSafe-Danger-Zone-Images: true`. El cos següent representa el mateix exemple sintètic dels triangles.

```json
{
  "model": "jev-1.14.0",
  "state": {
    "problem": "Solve the multiple-choice math problem. Return only one capital letter: A, B, C, D, or E.\nDo not include explanation or punctuation.\n\nQuants triangles blaus hi ha a la imatge?\n\nAnswer choices:\nA. 1\nB. 2\nC. 3\nD. 4\nE. 5",
    "problem_image": "61532d64-4dd4-4d1c-95af-9d8125d76391"
  },
  "questions": {
    "answer": {
      "type": "choice",
      "instructions": "Which answer letter is correct for `problem`? Return the single best choice. The choices may appear in `problem_image`.",
      "criteria": {
        "A": "Option A: 1",
        "B": "Option B: 2",
        "C": "Option C: 3",
        "D": "Option D: 4",
        "E": "Option E: 5"
      }
    }
  },
  "assets": {
    "61532d64-4dd4-4d1c-95af-9d8125d76391": {
      "type": "image",
      "format": "png",
      "data_base64": "BASE64_DEL_PNG"
    }
  }
}
```

`state.problem` conté el text que prepara el runner amb l'enunciat i les alternatives. `questions.answer` demana una única selecció. Les claus A–E de `criteria` són les respostes possibles i els valors provenen de les opcions seleccionades de la fila. El runner genera un UUID nou. `state.problem_image` fa de referència i la clau amb el mateix UUID dins d'`assets` conté la imatge. `data_base64` rep la codificació base64 sense prefix `data:`. En mode text o si la fila no té fitxer d'imatge, s'ometen `problem_image` i `assets`. La lletra es recupera a `answers.answer.choice`, on el servei també pot retornar les probabilitats i la confiança. [Runner](run.py) · [Imatges a System One](https://docs.typesafe.ai/images#sending-images-in-a-request) · [Resposta Choice](https://docs.typesafe.ai/primitives/choice#response-structure)

## Omni local

Liquid d1-omni-600M s'executa en CPU al Mac dins del procés Python, amb PyTorch i Transformers i els pesos del snapshot local. `omni.py` passa l'enunciat com a `state`, la pregunta `choice` amb les alternatives A–E com a `criteria` i, en visió, una llista amb un objecte PIL RGB a `model.system_one(state, {"answer": question}, images=images)`. El runner llegeix `answers.answer.choice` del resultat estructurat. La crida local no passa per l'API de TypeSafe. [Runner d’Omni](omni.py) · [Fitxa oficial del model](https://huggingface.co/LiquidAI/d1-omni-600M)

## Quantes imatges rep el model

Cada invocació de visió rep **zero o una imatge completa**. Les files successives són invocacions separades. A M3Kang, això són 72 adjunts al primer pilot i 32 a l'estratificat per passada de visió. Els PNG conserven les dimensions de les captures auditades. Al primer pilot fan entre 1653 i 1654 píxels d'ample i entre 116 i 465 d'alt. A l'estratificat, entre 1653 i 1700 d'ample i entre 148 i 422 d'alt. Els originals inicials fan 350 × 300 i tenen 6 PNG compartits per les tres traduccions, és a dir, 18 adjunts per passada. Els originals ampliats fan 800 × 500 i tenen 16 PNG, que es converteixen en 48 adjunts per passada. Les altres files sintètiques van només amb text.

En aquestes proves es va conservar la captura sencera perquè la figura, les etiquetes i les opcions gràfiques poden ocupar parts diferents del mateix PNG. El runner no en fa retalls ni envia cada alternativa per separat. A Qwen, el projector utilitza el pressupost visual configurat durant el processament. A Jev, els PNG originals caben d'una peça dins dels límits documentats de 280 a 1568 píxels per costat, 16 MiB per imatge i 32 MiB per cos HTTP. Els tokens visuals s'inclouen a `usage.input_tokens` de Jev. [Configuració de les proves](README.md) · [Límits d'imatge de TypeSafe](https://docs.typesafe.ai/images#limits)
