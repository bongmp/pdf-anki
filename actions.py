# actions.py
import json
import os
import fitz
from openai import OpenAI
import re
import streamlit as st
import streamlit.components.v1 as components
client = OpenAI()

# Custom component to call AnkiConnect on client side
parent_dir = os.path.dirname(os.path.abspath(__file__))
build_dir = os.path.join(parent_dir, "API/frontend/build")
_API = components.declare_component("API", path=build_dir)

def API(action, key=None, deck=None, front=None, back=None, tags=None):
    component_value = _API(action=action, key=key, deck=deck, front=front, back=back, tags=tags)
    return component_value

class Actions:
    def __init__(self, root):
        self.root = root

    # TODO: Extract pictures from PDF to add to flashcards.
    # TODO: Detect if page is mainly diagram and don't extract text.
    def check_API(self, key=None):
        print("API checked")
        response = API(action="reqPerm", key=key)
        if response is not False and response is not None:
            st.session_state['api_perms'] = response

    def get_decks(self, key=None):
        print("Decks checked")
        decks = API(action="getDecks", key=key)
        if decks is not False and decks is not None:
            st.session_state['decks'] = decks

    def send_to_gpt(self, page):
        # TODO: Make function call like mentioned in openai docs
        prompt = """
You are receiving the text from one slide of a lecture. Use the following principles when making the flashcards:

- Check if the slide contains specific keywords or phrases related to subject-specific information and call flashcard_function.
- If slide is just a table of contents, learning objectives, introductory or a title slide call null_function.
- If slide appears to only include a table or only a title call null_function.
- Create Anki flashcards for an exam at university level.
- Each card is standalone.
- Short answers.
- All information on slide needs to be used and only use the information that is on the slide.
- Answers should be on the back and not included in the question.
- Only add each piece of information once.
- Questions and answers must be in """ + st.session_state["lang"] + """.
- Ignore information about the uni, course, professor or auxiliary slide information.
- If whole slide fits on one flashcard, do that.
"""
        
        new_chunk = st.session_state['text_' + str(page)]
        new_chunk = prompt + 'Text: """\n' + new_chunk + '\n"""'

        behaviour = "You are a flashcard making assistant. Follow the user's requirements carefully and to the letter."

        if st.session_state['API_KEY'] == "":
            client.api_key = st.secrets['OPENAI_API_KEY']
        else:
            client.api_key = st.session_state['API_KEY']

        max_retries = 3
        retries = 0
        while retries < max_retries:
            completion = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": behaviour
                    },
                    {
                        "role": "user",
                        "content": new_chunk
                    }
                ],
                functions=[
                    {
                        "name": "flashcard_function",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "flashcards": {
                                    "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                            "front": {"type": "string", "description": "Front side of the flashcard; a question"},
                                            "back": {"type": "string", "description": "Back side of the flashcard; the answer"}
                                            }
                                        }
                                }
                            }
                        }  
                    },
                    {
                        "name": "null_function",
                        "parameters": {
                            "type": "object",
                            "properties": {}
                        }
                    }
                ],
                # TODO: play with temperature
                temperature=0.9,
            )
            
            # TODO: Make sure calls are not repeated
            print(f"Call no. {str(retries + 1)} for slide {str(page + 1)}")                
            if completion.choices[0].message.function_call:

                if completion.choices[0].message.function_call.name == "null_function": 
                    st.session_state[f"{str(page)}_is_title"] = True
                    return None

            try:
                response = completion.choices[0].message.function_call.arguments
                if response:
                    return response
            except AttributeError:
                print("Error: No function_call in the response. Retrying...")
                retries += 1
                continue

            raise Exception("Error: No completion response returned.")

    def add_to_anki(self, cards, page):
        deck = st.session_state['deck']
        try:
            # TODO: Process response from API
            for card in cards:
                front = card['front']
                back = card['back']
                tags = st.session_state["flashcards_" + str(page) + "_tags"]
                API("addCard", deck = deck, front = front, back = back, tags = tags)
            return True
        except Exception as e:
            raise ValueError(e)

    def cleanup_response(self, text):
        try:
            # Escape inner square brackets
            response_text_escaped = re.sub(r'(?<=\[)[^\[\]]*(?=\])', self.escape_inner_brackets, text)
            # print("Response text escaped:", response_text_escaped)

            # Replace curly quotes with standard double quotes
            response_text_standard_quotes = self.replace_curly_quotes(response_text_escaped)
            # print("Curly quotes removed:", response_text_standard_quotes)

            # Replace inner double quotes with single quotes
            response_text_single_quotes = re.sub(r'("(?:[^"\\]|\\.)*")', self.replace_inner_double_quotes, response_text_standard_quotes)
            # print("Double quotes removed:", response_text_single_quotes)

            # Parse the JSON data
            response_cards = json.loads(response_text_single_quotes, strict=False)
            # print("Parsed:", response_cards)            

            # Extract the "flashcards" array
            response_data = response_cards["flashcards"]

            return response_data

        except Exception as e:
            print(f"Error with OpenAI's GPT-3.5 Turbo: {str(e)}")
            print(text)

    def escape_inner_brackets(self, match_obj):
        inner_text = match_obj.group(0)
        escaped_text = inner_text.replace('[', '\\[').replace(']', '\\]')
        return escaped_text

    def replace_curly_quotes(self, text):
        return text.replace('“', "'").replace('”', "'").replace('„', "'")
    
    def replace_inner_double_quotes(self, match_obj):
        inner_text = match_obj.group(0)
        # Match the fields containing double quotes
        pattern = r'(:\s*)("[^"]*")'
        matches = re.findall(pattern, inner_text)

        # Replace the double quotes inside the fields with single quotes
        for match in matches:
            inner_quotes_replaced = match[1].replace('"', "'")
            inner_text = inner_text.replace(match[1], inner_quotes_replaced)

        return inner_text

    def detect_rects(self, page):
        # Credit for method to Jorj McKie (https://github.com/pymupdf/PyMuPDF-Utilities/blob/master/examples/extract-vector-graphics/separate-figures.py)
        """Detect and join rectangles of connected vector graphics."""
        # we need to exclude meaningless graphics that e.g. paint a white
        # rectangle on the full page.
        delta = (-1, -1, 1, 1)  # enlarge every path rect by this
        parea = abs(page.rect) * 0.8  # area of the full page (80%)

        # exclude graphics that are too large
        paths = [p for p in page.get_drawings() if abs(p["rect"]) < parea]

        # make a list of vector graphics rectangles (IRects are sufficient)
        prects = sorted(
            [(p["rect"] + delta).irect for p in paths], key=lambda r: (r.y1, r.x0)
        )

        new_rects = []  # the final list of the joined rectangles

        # -------------------------------------------------------------------------
        # The strategy is to identify and join all rects that have at least one
        # point in common.
        # -------------------------------------------------------------------------
        while prects:  # the algorithm will empty this list
            prects_len = len(prects)  # current list length
            r = prects[0]  # first rectangle
            repeat = True
            while repeat:
                for i in range(prects_len - 1, -1, -1):  # back to front
                    if i == 0:  # don't touch first rectangle
                        continue
                    if r.intersects(prects[i]):
                        r |= prects[i]  # join in to first rect
                        prects[0] = +r  # copy to list item
                        del prects[i]  # delete this rect
                prects_len = len(prects)  # length may have changed

                # This is true if remainings touch the updated first one.
                # Otherwise the while ends.
                repeat = any([r.intersects(prects[i]) for i in range(1, prects_len)])

            # move first item over to result list
            new_rects.append(prects.pop(0))
            prects = sorted(list(set(prects)), key=lambda r: (r.y1, r.x0))

        new_rects = sorted(list(set(new_rects)), key=lambda r: (r.y1, r.x0))
        return [r for r in new_rects if r.width > 5 and r.height > 5]
    
    def recoverpix(self, doc, item):
        # Credit for method to Jorj X. McKie (https://github.com/pymupdf/PyMuPDF-Utilities/blob/master/examples/extract-images/extract-from-pages.py)
        xref = item[0]  # xref of PDF image
        smask = item[1]  # xref of its /SMask

        # special case: /SMask or /Mask exists
        if smask > 0:
            pix0 = fitz.Pixmap(doc.extract_image(xref)["image"])
            if pix0.alpha:  # catch irregular situation
                pix0 = fitz.Pixmap(pix0, 0)  # remove alpha channel
            mask = fitz.Pixmap(doc.extract_image(smask)["image"])

            try:
                pix = fitz.Pixmap(pix0, mask)
            except:  # fallback to original base image in case of problems
                pix = fitz.Pixmap(doc.extract_image(xref)["image"])

            # if the image has an alpha channel, create a new image without the alpha channel
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)

            return {  # create dictionary expected by caller
                "colorspace": pix.colorspace.n,
                "image": pix.tobytes("jpg", jpg_quality=80),
            }

        # special case: /ColorSpace definition exists
        # to be sure, we convert these cases to RGB PNG images
        if "/ColorSpace" in doc.xref_object(xref, compressed=True):
            pix = fitz.Pixmap(doc, xref)
            pix = fitz.Pixmap(fitz.csRGB, pix)
            return {  # create dictionary expected by caller
                "ext": "png",
                "colorspace": 3,
                "image": pix.tobytes("jpg", jpg_quality=80),
            }
        return doc.extract_image(xref)
