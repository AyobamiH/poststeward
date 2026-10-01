import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ocpf_post.product_runtime import apply_environment, PRODUCT_LINEAGE
from ocpf_post.onboarding import import_project
from ocpf_post.preparation_import import import_preparation
from ocpf_post.campaigns import builtin_text, builtin_manifest, destination_binding


class PreparationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        env=patch.dict(os.environ,{'POSTSTEWARD_RUNTIME_LINEAGE':PRODUCT_LINEAGE,'POSTSTEWARD_RUNTIME_CONFIG_DIR':str(self.root/'config'),'POSTSTEWARD_RUNTIME_STATE_DIR':str(self.root/'state'),'POSTSTEWARD_SETUP_STATE_DIR':str(self.root/'setup')})
        env.start();self.addCleanup(env.stop);apply_environment()
        project={'schema_version':1,'project':'brand','label':'Brand','campaign_prefixes':['BRAND-'],
          'accounts':{'brand-x':{'provider':'x','account_id':'101','label':'Brand X'}},'default_accounts':{'x':'brand-x'}}
        path=self.root/'project.json';path.write_text(json.dumps(project));preview=import_project(path)
        import_project(path,apply=True,expected_sha256=preview['input_sha256'])
        self.export={'workspace':'workspace-1','preparation':{'id':'preparation-1','revision':1,'status':'handed_off','campaign':'a'*64,
          'channels':[{'alias':'cloud_x','provider':'x','binding':1,'identityId':'101'}],
          'drafts':[{'alias':'cloud_x','text':'Exact approved text 東京 café.\n\nSecond paragraph remains exact.'}]}}
        self.path=self.root/'export.json';self.path.write_text(json.dumps(self.export));self.path.chmod(0o600)
        self.cloud={'workspace':'workspace-1','accounts':[{'alias':'cloud_x','provider':'x','version':1,'active':True,'identity':{'id':'101'}}]}
        mock=patch('ocpf_post.preparation_import.bindings',return_value=self.cloud);mock.start();self.addCleanup(mock.stop)
        self.options={'project':'brand','campaign':'BRAND-001','variant':'cloud_x','account':'brand-x'}

    def test_preview_apply_exact_text_and_repeat_remain_manual_only(self):
        preview=import_preparation(self.path,**self.options)
        self.assertIsNone(builtin_text('BRAND-001','x'))
        applied=import_preparation(self.path,**self.options,apply=True,expected_sha256=preview['review_sha256'])
        self.assertFalse(applied['published']);self.assertFalse(applied['allocation_enabled'])
        self.assertEqual(builtin_text('BRAND-001','x'),self.export['preparation']['drafts'][0]['text'])
        self.assertFalse(builtin_manifest('BRAND-001')['allocation']['enabled'])
        repeated=import_preparation(self.path,**self.options,apply=True,expected_sha256=preview['review_sha256'])
        self.assertEqual(repeated['result'],'already_present')

    def test_wrong_workspace_unapproved_copy_and_changed_bindings_are_blocked(self):
        for change in ('workspace','status','identity','version'):
            value=json.loads(json.dumps(self.export))
            if change=='workspace':value['workspace']='other'
            elif change=='status':value['preparation']['status']='review'
            elif change=='identity':value['preparation']['channels'][0]['identityId']='wrong'
            else:value['preparation']['channels'][0]['binding']=2
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):import_preparation(self.path,**self.options)
            self.assertIsNone(builtin_text('BRAND-001','x'))

    def test_changed_text_or_target_cannot_reuse_review_digest(self):
        preview=import_preparation(self.path,**self.options)
        self.export['preparation']['drafts'][0]['text']='Changed after review'
        self.path.write_text(json.dumps(self.export))
        with self.assertRaises(ValueError):import_preparation(self.path,**self.options,apply=True,expected_sha256=preview['review_sha256'])
        self.assertIsNone(builtin_text('BRAND-001','x'))
        with self.assertRaises(ValueError):import_preparation(self.path,**{**self.options,'campaign':'BRAND-002'},apply=True,expected_sha256=preview['review_sha256'])

    def test_imported_identity_is_fenced_after_local_registry_drift(self):
        preview=import_preparation(self.path,**self.options)
        import_preparation(self.path,**self.options,apply=True,expected_sha256=preview['review_sha256'])
        with patch('ocpf_post.campaigns.resolve_account',return_value={'account_id':'different','provider':'x'}):
            self.assertIsNone(destination_binding('BRAND-001','x'))

if __name__=='__main__':unittest.main()
