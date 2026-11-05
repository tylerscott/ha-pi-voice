# pi-voice Conversation for Home Assistant

A Home Assistant conversation agent that forwards Assist utterances to a
[pi-voice](https://github.com/earendil-works/pi) durable agent over HTTP. Pick it as
the conversation agent in a voice assistant pipeline and your wake word, speech to
text and text to speech stay in Home Assistant while the thinking happens in pi-voice.

## Install

1. HACS → Integrations → ⋮ → Custom repositories → add this repository as type *Integration*.
2. Install **pi-voice Conversation** and restart Home Assistant.
3. Settings → Devices & services → Add integration → *pi-voice Conversation*.
   Enter the agent URL, for example `http://192.168.0.33:8787`, and the API key if pi-voice has one.
4. Settings → Voice assistants → your assistant → Conversation agent → *pi-voice*.

## How a turn works

- The utterance is sent to `POST {url}/api/conversation` together with the Assist
  `conversation_id`, language, the voice device's name and area, and the best music
  player in that area. pi-voice keeps its own durable memory across turns.
- If the agent has not answered within the hand-off window (default 15 s, Assist
  cancels turns at about 30 s) the satellite says it is working on it. When the
  answer arrives it is announced on that satellite. If the answer ends in a
  question the satellite starts listening again.
- Replies stream: text-to-speech starts on the first sentence while the agent is still finishing.
- A reply that ends in a question mark keeps the microphone open.

## Options

Instructions (sent to the agent as standing instructions), hand-off seconds, and
request timeout.
