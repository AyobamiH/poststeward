import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.installed_lifecycle import lifecycle, tree_digest


class InstalledLifecycleTests(unittest.TestCase):
    def test_digest_matches_manifest_global_name_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'a').mkdir();(root/'a'/'z').write_text('one');(root/'a-b').write_text('two')
            expected=hashlib.sha256()
            for name in ['a-b','a/z']:
                data=(root/name).read_bytes()
                expected.update(name.encode()+b'\0'+str(len(data)).encode()+b'\0'+hashlib.sha256(data).hexdigest().encode()+b'\n')
            self.assertEqual(tree_digest(root),expected.hexdigest())

    def test_reviewed_uninstall_retains_data_and_refuses_changed_receipt(self):
        with tempfile.TemporaryDirectory(prefix="Owner's machine ") as temp:
            root=Path(temp);prefix=root/'data'/'poststeward';binary=root/'bin';revision='a'*40
            release=prefix/'releases'/revision;release.mkdir(parents=True);binary.mkdir();(release/'poststeward').write_text('runtime')
            (prefix/'current').symlink_to(release);(binary/'poststeward').write_text('# managed-by: poststeward-installer\n')
            receipt=root/'state'/'poststeward'/'install.json';receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps({'product':'poststeward','resolved_revision':revision,'release_path':str(release),
                'install_prefix':str(prefix),'bin_dir':str(binary),'runtime_tree_sha256':tree_digest(release)}))
            data=root/'state'/'poststeward'/'runtime';data.mkdir();(data/'receipt.json').write_text('durable evidence')
            with patch.dict(os.environ,{'XDG_STATE_HOME':str(root/'state'),'XDG_CONFIG_HOME':str(root/'config')}),patch('ocpf_post.installed_lifecycle.require_inactive'):
                review=lifecycle('uninstall',retain_data=True)
                with self.assertRaises(ValueError):lifecycle('uninstall',retain_data=True,apply=True,expected_sha256='wrong')
                self.assertTrue((binary/'poststeward').exists())
                lifecycle('uninstall',retain_data=True,apply=True,expected_sha256=review['review_sha256'])
                self.assertFalse((binary/'poststeward').exists());self.assertEqual((data/'receipt.json').read_text(),'durable evidence')

    def test_canonical_keyring_namespace_does_not_share_original_post_once(self):
        from ocpf_post.credential_keyring import _service
        with patch.dict(os.environ,{'POSTSTEWARD_RUNTIME_LINEAGE':'poststeward-local-runtime-v1'}):
            self.assertEqual(_service(),'poststeward/local-runtime')
        with patch.dict(os.environ,{},clear=True):
            self.assertEqual(_service(),'oneclickpostfactory/post-once')


if __name__=='__main__':unittest.main()
