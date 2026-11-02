from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VercelRoutingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.app = (ROOT / "app.html").read_text(encoding="utf-8")
        cls.app_core = (ROOT / "app_core.html").read_text(encoding="utf-8")
        cls.gateway = (ROOT / "api" / "cime.py").read_text(encoding="utf-8")
        cls.worker = (ROOT / "service-worker.js").read_text(encoding="utf-8")
        cls.server = (ROOT / "server_v5.py").read_text(encoding="utf-8")

    def test_frontend_uses_canonical_api_paths(self):
        start = self.index.index("function cimeBackendPath(path){")
        end = self.index.index("\n}", start) + 2
        helper = self.index[start:end]
        self.assertIn("return String(path||'');", helper)
        self.assertNotIn("return '/api/cime?__cime_path='", helper)

    def test_gateway_ignores_gateway_sentinel_when_restoring_route(self):
        self.assertIn("candidates=[str(value or \"\").strip() for value in q.get(\"__cime_path\",[])]", self.gateway)
        self.assertIn("(value.rstrip(\"/\") or \"/\")!=\"/api/cime\"", self.gateway)

    def test_qr_route_has_gateway_fallback(self):
        self.assertIn("def _qr_fallback_get(self):", self.gateway)
        self.assertIn('!= "/api/qr":', self.gateway)
        self.assertIn("if self._qr_fallback_get():", self.gateway)

    def test_public_health_qr_and_persistence_routes_use_gateway(self):
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        rewrites = {rule["source"]: rule["destination"] for rule in config["rewrites"]}
        for path in ("/api/health", "/api/cime_health", "/api/qr", "/api/persistence/health"):
            self.assertNotIn(path, rewrites)
        self.assertEqual(rewrites.get("/api/:path*"), "/api/cime?__cime_path=/api/:path*")
        self.assertIn("if self._health_fallback_get():", self.gateway)
        self.assertIn('path == "/api/persistence/health"', self.gateway)

    def test_details_panel_uses_its_single_close_control(self):
        self.assertIn("data-panel-close", self.app)
        self.assertNotIn('$("closeDetails").onclick', self.app)

    def test_logo_is_deployed_as_a_public_asset_and_cannot_block_sw_install(self):
        self.assertTrue((ROOT / "public" / "cime-logo.svg").is_file())
        self.assertIn("Promise.allSettled(OPTIONAL_SHELL", self.worker)
        self.assertIn("/cime-logo.svg", self.worker)

    def test_email_invite_survives_login_and_is_accepted(self):
        self.assertIn('get("invite")', self.index)
        self.assertIn("cime5PendingInvite", self.index)
        self.assertIn("/api/invites/accept", self.app)
        self.assertIn("public_app_base(self)}/?invite={raw}", self.server)
        self.assertIn("public_app_base(self)}/?group_share={raw}", self.server)

    def test_research_button_only_updates_names_and_missing_short_synopses(self):
        self.assertIn("mode=names_synopses", self.app)
        self.assertIn("match_score>=92", self.server)
        self.assertIn("_simple_synopsis", self.server)
        enrich = self.server[self.server.index("def _safe_enrich_title("):self.server.index("Gateway.read_body =")]
        self.assertNotIn("updates['rating']", enrich)
        self.assertIn("if not row.get('synopsis_pt')", enrich)

    def test_panel_displays_retry_when_episode_history_fails(self):
        self.assertIn("data-panel-retry", self.app)
        self.assertIn("Não foi possível carregar temporadas e episódios agora.", self.app)

    def test_episode_controls_use_only_the_selected_season_total(self):
        self.assertIn("function currentSeasonEpisodeTotal(x,season=x?.current_season)", self.app)
        self.assertIn("const currentTotal=currentSeasonEpisodeTotal(x,currentSeason);", self.app)
        self.assertIn("const currentTotal=currentSeasonEpisodeTotal(x,cs);", self.app_core)
        self.assertNotIn("currentInfo?.episodes||x.episodes||0", self.app)
        self.assertIn("${currentTotal?`<div class=\"episode-track\"", self.app)

    def test_movie_cards_hide_season_labels_and_next_episode_controls(self):
        self.assertIn('function isFilmTitle(x){', self.app)
        self.assertIn('function franchiseLabel(x){if(isFilmTitle(x))return"";', self.app)
        self.assertIn('function nextPosition(x){if(isFilmTitle(x))return null;', self.app)
        self.assertIn('function franchiseLabel(x){if(isFilmTitle(x))return"";', self.app_core)
        self.assertIn('function nextPosition(x){if(isFilmTitle(x))return null;', self.app_core)

    def test_progress_filter_keeps_unknown_separate_from_zero_percent(self):
        self.assertIn('a.progress==="unknown"?p==null', self.app)
        self.assertIn('a.progress==="none"?p===0', self.app)
        self.assertIn('option value="unknown">Progresso pendente ou total desconhecido', self.app)
        self.assertIn('option value="none">Sem progresso (0%)', self.app)

    def test_details_still_render_personal_rating_and_written_review(self):
        self.assertIn('field("Nota pessoal",x.rating', self.app)
        self.assertIn('String(x.notes||"").trim()', self.app)
        self.assertIn("📝 Minha avaliação</h3>", self.app)

    def test_all_mutating_get_routes_are_persisted(self):
        self.assertIn("def _is_write_request(method, path):", self.gateway)
        self.assertIn('"/api/library/enrich"', self.gateway)
        self.assertIn('"/api/library/covers"', self.gateway)
        self.assertIn('"/api/auth/status"', self.gateway)
        self.assertIn('"/api/bootstrap"', self.gateway)
        self.assertIn('write_request = _is_write_request(method, path)', self.gateway)

    def test_oauth_fallback_does_not_bypass_persistence_wrapper(self):
        self.assertIn("Do not handle OAuth here: direct responses bypass the persistent", self.gateway)
        self.assertIn("with request_context(write=True) as store:", self.gateway)

    def test_backend_routing_module_parses(self):
        import ast
        ast.parse(self.gateway)


if __name__ == "__main__":
    unittest.main()
