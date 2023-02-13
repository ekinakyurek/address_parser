import logging
import os
import re
from functools import lru_cache
from typing import List
from fastapi import FastAPI, HTTPException
import converter
from config import Settings
from logger import setup_logging
from models import IntentResponse, RequestIntent
from tokenizer import GPTTokenizer


setup_logging()
app = FastAPI()


@lru_cache(maxsize=None)
def get_settings(pid: int):
    settings = Settings()

    with open(settings.address_prompt_file) as handle:
        settings.address_template = handle.read()

    # with open(settings.intent_prompt_file) as handle:
    #     settings.intent_template = handle.read()

    # with open(settings.detailed_intent_prompt_file) as handle:
    #     settings.detailed_intent_template = handle.read()

    with open(settings.detailed_intent_prompt_file_v2) as handle:
        settings.detailed_intent_template_v2 = handle.read()

    if settings.geo_location:
        settings.geo_key = converter.setup_geocoding()

    converter.setup_openai(pid % settings.num_workers)

    logging.warning(f"Engine {settings.engine}")

    return settings


def convert(
    info: str,
    inputs: List[str],
    settings: Settings,
):
    if info == "address":
        template = settings.address_template
        max_tokens = settings.address_max_tokens
        temperature = 0.1
        frequency_penalty = 0.3
    # elif info == "intent":
    #     template = settings.intent_template
    #     max_tokens = settings.intent_max_tokens
    #     temperature = 0.0
    #     frequency_penalty = 0.0
    # elif info == "detailed_intent":
    #     template = settings.detailed_intent_template
    #     max_tokens = settings.detailed_intent_max_tokens
    #     temperature = 0.0
    #     frequency_penalty = 0.0
    elif info == "detailed_intent_v2":
        template = settings.detailed_intent_template_v2
        max_tokens = settings.detailed_intent_max_tokens_v2
        temperature = 0.0
        frequency_penalty = 0.0
    else:
        raise ValueError("Unknown information extraction requested")

    def preprocess_tweet(text: str) -> str:
        mention_pattern = r"@\w+"
        url_pattern = r"(\w+?://)?(?:www\.)?[-a-zA-Z0-9@:%._\\+~#=]{1,256}\.[a-zA-Z]{1,10}\b(?:[-a-zA-Z0-9()@:%_\\+.~#?&\\/=]*)"
        # remove mentions
        mentions_removed = re.sub(mention_pattern, " ", text)
        # remove urls
        url_removed = re.sub(url_pattern, "", mentions_removed)
        # remove consequent spaces
        return re.sub(r"\s+", " ", url_removed)

    def create_prompt(text, template) -> str:
        template_token_count = GPTTokenizer.token_count(template)
        truncated_text = GPTTokenizer.truncate(
            preprocess_tweet(text),
            max_tokens=GPTTokenizer.MAX_TOKENS - template_token_count,
        )
        return template.format(ocr_input=truncated_text)

    text_inputs = []
    for tweet in inputs:
        text_inputs.append(create_prompt(text=tweet, template=template))

    outputs = converter.query_with_retry(
        text_inputs,
        engine=settings.engine,
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=1,
        frequency_penalty=frequency_penalty,
        presence_penalty=0,
        stop="#END",
    )

    returned = []
    for output in outputs:
        returned_dict = {}
        returned_dict["string"] = output
        try:
            returned_dict["processed"] = converter.postprocess(info, output[0])
        except Exception as e:
            returned_dict["processed"] = {
                "intent": [],
                "detailed_intent_tags": [],
            }
            logging.warning(f"Parsing error in {output},\n {e}")

        if info == "address" and settings.geo_location and returned_dict["processed"]:
            returned_dict["processed"]["geo"] = converter.get_geo_result(
                settings.geo_key, returned_dict["processed"]
            )
        returned.append(returned_dict)

    return returned


@app.post("/intent-extractor/", response_model=IntentResponse)
async def intent(payload: RequestIntent):
    try:
        pid = int(os.getpid())
        settings = get_settings(pid)
        inputs = payload.dict()["inputs"]
        outputs = convert("detailed_intent_v2", inputs, settings)
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"An error occurred: {error}")

    return {"response": outputs}


@app.get("/health")
async def health():
    return {"status": "living the dream"}
