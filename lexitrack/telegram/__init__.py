"""The Telegram client: a thread inside the app that talks to the same services.

``config`` finds the token, ``messages`` says things, ``schedule`` decides when
to speak unprompted, ``core`` turns commands and button presses into engine
calls, and ``runtime`` connects all of it to Telegram over long polling.
"""
