from __future__ import annotations

import re
import unittest

from ocpf_post.publication_payload import build_publication, split_text, x_weighted_length


class PublicationPayloadTests(unittest.TestCase):
    def _normalise(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def test_long_x_copy_becomes_thread_without_losing_text(self) -> None:
        text = (
            "A consequential agent action should leave evidence of what authority existed, "
            "what changed, and what external effect actually happened. "
            "That receipt should be independently inspectable rather than inferred from a green command.\n\n"
            "This is the second idea. It explains why exactly-once delivery matters when provider responses "
            "can become uncertain after a write has already started.\n\n"
            "This final section keeps the complete approved thought and a useful question for the reader."
        )
        publication = build_publication("x", text)
        self.assertEqual(publication["publication_type"], "thread")
        self.assertGreater(publication["part_count"], 1)
        self.assertTrue(all(len(row["text"]) <= 275 for row in publication["parts"]))
        reconstructed = " ".join(row["text"] for row in publication["parts"])
        self.assertEqual(self._normalise(reconstructed), self._normalise(text))
        self.assertNotIn("…", reconstructed)

    def test_x_uses_weighted_length_for_dense_unicode(self) -> None:
        text = "猫" * 200
        publication = build_publication("x", text)
        self.assertEqual(publication["publication_type"], "thread")
        self.assertTrue(all(x_weighted_length(row["text"]) <= 275 for row in publication["parts"]))
        self.assertEqual("".join(row["text"] for row in publication["parts"]), text)

    def test_x_keeps_long_transformed_url_atomic(self) -> None:
        url = "https://example.com/" + "a" * 400
        self.assertEqual(x_weighted_length(url), 23)
        publication = build_publication("x", url)
        self.assertEqual(publication["publication_type"], "single")
        self.assertEqual(publication["parts"][0]["text"], url)

    def test_long_threads_copy_becomes_reply_chain(self) -> None:
        text = " ".join(f"word{i}" for i in range(180))
        publication = build_publication("threads", text)
        self.assertEqual(publication["publication_type"], "thread")
        self.assertGreater(publication["part_count"], 1)
        self.assertTrue(all(len(row["text"]) <= 500 for row in publication["parts"]))
        reconstructed = " ".join(row["text"] for row in publication["parts"])
        self.assertEqual(self._normalise(reconstructed), self._normalise(text))

    def test_short_copy_stays_single(self) -> None:
        for provider in ("x", "threads", "linkedin"):
            with self.subTest(provider=provider):
                publication = build_publication(provider, "Short approved copy.")
                self.assertEqual(publication["publication_type"], "single")
                self.assertEqual(publication["part_count"], 1)

    def test_whole_publication_envelope_fails_closed_instead_of_truncating(self) -> None:
        with self.assertRaisesRegex(ValueError, "refusing to truncate"):
            build_publication("x", "x" * 7000)

    def test_splitter_never_returns_oversized_or_empty_parts(self) -> None:
        parts = split_text("alpha " * 300, 100)
        self.assertTrue(parts)
        self.assertTrue(all(part and len(part) <= 100 for part in parts))


if __name__ == "__main__":
    unittest.main()
