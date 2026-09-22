import unittest

from gateway.protocol import SentenceBuffer, speakable_text


class ProtocolTests(unittest.TestCase):
    def test_sentence_buffer(self):
        buffer = SentenceBuffer()
        self.assertEqual(buffer.push("Hello there. How"), ["Hello there."])
        self.assertEqual(buffer.push(" are you?"), ["How are you?"])
        self.assertEqual(buffer.flush(), "")

    def test_speakable_text_strips_markup_and_urls(self):
        text = speakable_text("**Open** https://example.com now")
        self.assertEqual(text, "Open a link now")


if __name__ == "__main__":
    unittest.main()
