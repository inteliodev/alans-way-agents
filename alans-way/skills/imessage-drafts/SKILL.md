---
name: imessage-drafts
description: "Read Hayden's iMessage and store reply drafts. Sending one stored draft requires his approval."
version: 0.1.0
author: capthvnsen, Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [imessage, drafts, approvals]
    related_skills: []
---

# iMessage drafts

Hayden's personal iMessage is reachable through a BlueBubbles server on his
Mac, over Tailscale only. These tools are a drafts inbox. They are not the
Hermes `bluebubbles` gateway platform. Do not enable that platform, do not
configure it, and do not register a webhook. That adapter would answer his
contacts as if the Apple ID were a bot.

## When Hayden asks

Read chats and messages when he asks, and store a reply draft when he asks
for one. Show every draft back to him in the conversation: the draft id, the
recipient display name, the chat guid, the full text, and `status: pending`.

`imessage_draft_reply` never sends. A change of wording is a new draft. Do
not try to edit a stored draft, and do not invent a free-form send tool.
`imessage_send_draft` takes only a draft id and posts that stored text
unchanged.

## Sending

Call `imessage_send_draft` only after Hayden has asked to send that exact
draft. The tool asks Hermes for native approval. He gets an Approve/Deny
prompt in Telegram or the app that shows the recipient and the full text.
Silence, timeout, and Deny send nothing. Do not retry a denied or timed-out
send unless he asks again.

Never contact anyone proactively. Do not draft or send because a watch fired,
because a chat is unread, or because a reply seems helpful. Unread is
information for him, not a reason to message the other person.

## What you do not do

- Do not pass a message body to the send tool. It has no text argument.
- Do not send to a chat that did not resolve to exactly one existing chat.
- Do not create a new chat, start a group, or message a handle with no thread.
- Do not read `BLUEBUBBLES_PASSWORD` aloud, copy it into a tool argument, or
  include it in a message. The server URL must stay a tailnet or loopback
  address already configured in the profile `.env`.
