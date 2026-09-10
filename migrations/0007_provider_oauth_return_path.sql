ALTER TABLE provider_oauth_states ADD COLUMN return_path TEXT NOT NULL DEFAULT '/pilot' CHECK (return_path IN ('/pilot','/app'));
