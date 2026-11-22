# Marketplace Camoufox Agent

Samostatný Python projekt pro automatizaci práce s Facebook Marketplace v **Camoufoxu** místo Dolphin Anty. Obsahuje sedm izolovaných profilů, plánování unikátních inzerátů v hlavním vysílacím čase, zpracování fotografií a Excelu, evidence kontaktů z Messengeru a nastavených příspěvků ve skupinách, JSON webhook, CSV export a omezeného AI asistenta pro opravu selektorů.

> **Stav projektu:** jádro, databáze, plánování, zpracování Excelu a fotografií, webhook i AI vrstva jsou implementované. Živé Facebook rozhraní se liší podle jazyka, účtu a právě probíhajících změn. Selektory proto musí projít kalibrací a kontrolovaným suchým během na vašich účtech. Repozitář netvrdí, že proběhl ostrý end-to-end test s reálnými Facebook účty.

## Co projekt řeší

Výchozí zadání bylo: Windows PC, sedm oddělených profilů, jedno domácí připojení a šest statických CZ ISP proxy, tři unikátní inzeráty denně na profil, dvě fotografie na inzerát, odstranění EXIF údajů, evidence telefonních čísel a odeslání leadů přes POST JSON webhook.

Projekt k tomu přidává:

- trvalý samostatný profil Camoufoxu pro každou identitu, včetně cookies a přihlášení;
- proxy a GeoIP pro daný profil, bez sdílení prohlížečového úložiště;
- zámek zabraňující dvěma souběžným prohlížečům na stejném profilu;
- SQLite frontu, deduplikaci inzerátů i leadů a obnovu po pádu procesu;
- plánovač s časovými okny, minimálními odstupy a omezenou paralelností;
- bezpečný výchozí režim, který vyplní formulář, ale nepublikuje;
- stav `unknown`, který po nejistém kliknutí na Publikovat zabrání slepému opakování;
- validaci telefonních čísel a normalizaci do formátu E.164;
- webhook s HMAC-SHA256 podpisem, opakováním odeslání a idempotenčním klíčem;
- AI návrhy nových selektorů, které musí projít kontrolou v prohlížeči i posouzením operátora;
- týdenní AI přehled spolehlivosti nad agregovanými metrikami, nikoliv samovolné přepisování produkce.

## Důležitá omezení a bezpečný provoz

Software používejte pouze tam, kde máte oprávnění a kde je automatizace v souladu s aktuálními podmínkami Mety, pravidly Marketplace a právními předpisy o osobních údajích. Za toto posouzení odpovídá provozovatel.

Projekt **neobchází** CAPTCHA, checkpointy, přihlašovací ověření ani omezení platformy. Při jejich zjištění se zastaví pro ruční řešení. Neobsahuje automatické odpovídání cizím lidem, hromadné zprávy, falešnou aktivitu, „warm-up“ účtů, automatické vstupování do skupin ani obcházení blokací.

Sběr kontaktů je určen pro telefonní čísla, která lidé dobrovolně uvedou v nastavených Messenger konverzacích nebo komentářích pod konkrétním příspěvkem. Nejde o nástroj na plošné stahování údajů z profilů. Text zpráv se ve výchozím nastavení neukládá.

Ostré publikování i sběr vyžadují dvě současná povolení:

1. `runtime.live: true` v konfiguraci;
2. parametr `--live` na příkazové řádce.

Bez nich publikování probíhá pouze jako suchý běh.

## Jak systém funguje

```text
Excel + dvě fotky ──> validace ──> denní plánovač ──> SQLite fronta
                                            │
                                            v
                             trvalý profil Camoufoxu
                             (jedna proxy / jedna identita)
                                            │
                               suchý běh nebo publikace
                                            v
                           published / failed / unknown

Messenger + nastavené příspěvky ──> rozpoznání dobrovolně uvedeného čísla
                                  ──> deduplikovaná SQLite evidence
                                  ──> podepsaný JSON webhook a CSV

Chyba selektoru ──> očištěný seznam ovládacích prvků ──> strukturovaný AI návrh
                  ──> ověření v prohlížeči ──> návrh operátorovi do YAML
```

Běžný běh je deterministický: stejné vstupy vedou ke stejným akcím podle YAML pravidel. AI se používá až při poruše nebo pro analytický přehled. Nedostane volnou kontrolu nad prohlížečem, nemůže publikovat, měnit limity, přepisovat konfiguraci ani si rozšiřovat oprávnění.

## Proč Camoufox a hybridní agent

Rešerše porovnávala čisté Playwright skripty a plně autonomní prohlížečové agenty. Samotné LLM ovládání je vhodné na průzkum, ale u opakovaných obchodních transakcí je pomalejší a hůře předvídatelné. Proto projekt používá hybridní způsob známý například z [Stagehandu](https://github.com/browserbase/stagehand): normální cestu obsluhují pevné Playwright akce a model pomáhá pouze s významem prvků při změně stránky. Odpovědi modelu jsou validované pomocí JSON Schema podle [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs).

Camoufox je použit přes oficiální asynchronní `AsyncCamoufox` API s `persistent_context=True`, `user_data_dir`, proxy a GeoIP. Podrobnosti jsou v dokumentaci [Camoufoxu](https://camoufox.com/python/usage/) a [Playwright persistent contexts](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context). Camoufox sám uvádí, že je stále ve vývoji; každou aktualizaci proto nejprve ověřte kontrolovaným suchým během.

## Instalace na Windows

Potřebujete Python 3.11 nebo novější a [uv](https://docs.astral.sh/uv/). V PowerShellu:

```powershell
git clone https://github.com/david-strejc/marketplace-camoufox-agent.git
cd marketplace-camoufox-agent
uv sync --all-extras
uv run camoufox fetch
Copy-Item config.example.yaml config.yaml
```

`camoufox fetch` stáhne potřebnou verzi prohlížeče. Nepoužívejte svůj běžný Firefox profil. Aplikace vytvoří oddělené adresáře pod `profiles/`.

## Konfigurace sedmi profilů

Připravený `config.example.yaml` obsahuje:

- `home` bez proxy pro domácí připojení;
- `cz-01` až `cz-06` se šesti samostatnými proxy endpointy;
- české prostředí `cs-CZ` a časovou zónu `Europe/Prague`;
- časová okna 11:30–14:00 a 17:00–21:00;
- výchozí limit tři inzeráty na profil a den;
- minimální odstup 45 minut a nejvýše dva souběžné prohlížeče.

Loopback adresy `127.0.0.1:8101` až `8106` jsou pouze nefunkční bezpečné placeholdery. Nahraďte je vlastními statickými CZ proxy endpointy.

Proxy s přihlášením se konfiguruje přes proměnné prostředí:

```yaml
proxy:
  server: "${FB_PROXY_CZ_01_SERVER}"
  username: "${FB_PROXY_CZ_01_USERNAME}"
  password: "${FB_PROXY_CZ_01_PASSWORD}"
```

Podporované jsou HTTP, HTTPS a SOCKS5 endpointy. Při nastavené proxy se zapne GeoIP. Skutečné přihlašovací údaje nepište do Git repozitáře; `config.yaml`, `.env`, profily, databáze a artefakty jsou v `.gitignore`.

## První přihlášení

Přihlášení probíhá ručně a prohlížeč musí být viditelný:

```powershell
uv run marketplace-agent --config config.yaml login --profile home
uv run marketplace-agent --config config.yaml login --profile cz-01
```

V otevřeném okně se přihlaste, případné ověření dokončete ručně, vrátíte se do PowerShellu a stisknete Enter. Session zůstane v adresáři daného profilu. Tento postup opakujte pro všech sedm profilů. Adresář `profiles/` obsahuje citlivé cookies a nikdy jej nesdílejte.

## Struktura Excelu

Vytvořte `data/listings.xlsx` se listy `listings`, `cities` a `responses`. Relativní cesty k fotografiím se počítají od umístění Excelu.

### List `listings`

| Sloupec | Povinný | Význam |
|---|---:|---|
| `id` | ano | Stabilní globálně unikátní identifikátor inzerátu |
| `title` | ano | Název |
| `description` | ano | Popis |
| `price` | ano | Kladné desetinné číslo |
| `city` | ano | Město odpovídající povolenému seznamu |
| `category` | ne | Přebije `default_category` z YAML |
| `photo_1` | ano | První připravená fotografie |
| `photo_2` | ano | Druhá připravená fotografie |

Prázdné řádky se přeskočí. Duplicitní ID, neplatná cena, chybějící město, město mimo seznam nebo neexistující fotka zastaví validaci ještě před spuštěním prohlížeče.

Každé ID se přidělí pouze jednomu profilu. Tím se zabrání náhodnému použití stejného inzerátu na všech sedmi účtech.

### List `cities`

Obsahuje sloupec `city`. Slouží jako seznam povolených měst.

### List `responses`

Obsahuje sloupce `key` a `text`. Klíče musí být unikátní a texty neprázdné. Šablony projdou validací, ale aplikace je záměrně sama neodesílá do Messengeru. Slouží jako připravený podklad pro operátora nebo navazující schválený proces.

## Příprava tisíců fotografií

Příkaz projde celý zdrojový adresářový strom, zachová podsložky a vytvoří čisté JPEG soubory:

```powershell
uv run marketplace-agent --config config.yaml prepare-images `
  --source "D:\Marketplace\originals" --output "D:\Marketplace\clean"
```

Zpracování:

1. načte běžné obrazové formáty;
2. použije orientaci uloženou v EXIF;
3. vytvoří nové RGB plátno bez původních metadat;
4. uloží JPEG bez EXIF;
5. existující výstup přeskočí místo přepsání.

Zdrojový a výstupní adresář musí být odlišné. V Excelu používejte právě dvě očištěné fotografie na každý řádek.

## Selektory Facebooku

Facebook mění DOM, texty a rozložení podle jazyka, účtu a postupných rolloutů. Příklad obsahuje české a anglické role pro název, cenu, popis, kategorii, město, fotky, Další a Zveřejnit. Před ostrým během je nutné je ověřit.

Podporované strategie:

- `role`, volitelně s `name`;
- `css`;
- `text`;
- `label`;
- `placeholder`.

Jeden záznam používá právě jednu strategii. Publikovací selektor musí najít právě jeden prvek. Obecné CSS selektory s více výsledky se záměrně nepoužijí.

Pro sběr kontaktů doplňte `messenger_threads`, `messenger_messages`, `group_items` a volitelně selektory odesílatele. **Selektor `messenger_messages` musí vybírat pouze příchozí zprávy, nikdy vaše vlastní odpovědi.** Skupiny se nastavují na konkrétních profilech přes `group_post_urls` s přesnými URL povolených příspěvků. Dokud selektory nejsou zkontrolované, nechte `collection.enabled: false`.

## Suchý běh

Nejprve ověřte konfiguraci, Excel a fotografie:

```powershell
uv run marketplace-agent --config config.yaml validate --content
uv run marketplace-agent --config config.yaml plan
uv run marketplace-agent --config config.yaml run
```

- `validate --content` zkontroluje YAML, profily, povinné sloupce, města, šablony, ceny i existenci fotek.
- `plan` rozdělí dosud nepoužité inzeráty do časových slotů. Sloty jsou deterministické pro datum a profil.
- `run` bez `--live` zpracuje právě splatné úlohy, otevře profil, vyplní formulář, ověří tlačítko Zveřejnit, ale neklikne na něj.

Suchý běh ponechá úlohy ve stavu `scheduled`, takže později mohou proběhnout ostře. Pokud žádný časový slot právě není splatný, výstup obsahuje nuly. Rozvrh zobrazí příkaz `plan`.

## Ostré publikování

Po schváleném suchém běhu nastavte v `config.yaml`:

```yaml
runtime:
  live: true
```

Potom:

```powershell
uv run marketplace-agent --config config.yaml run --live
uv run marketplace-agent --config config.yaml run --live --watch
```

První příkaz zpracuje splatné úlohy jednou. Druhý trvale plánuje a zpracovává další sloty. `watch` je vhodný pro dohlíženou úlohu Windows Task Scheduleru. Nepoužívejte souběžně více plánovačů nad stejnou instalací.

### Význam stavů

- `scheduled`: čeká na svůj slot;
- `running`: proces úlohu právě provádí;
- `published`: Facebook přesměroval na rozpoznanou potvrzovací URL;
- `failed`: běh skončil chybou před potvrzením; před opakováním zkontrolujte účet;
- `unknown`: na Zveřejnit se kliklo, ale potvrzení nedošlo. **Nejprve ručně ověřte, zda inzerát neexistuje. Nikdy tuto úlohu slepě neopakujte.**

Ověřenou neúspěšnou úlohu, která se skutečně nepublikovala, vrátíte do fronty:

```powershell
uv run marketplace-agent --config config.yaml retry-job 42
```

Přerušené `running` úlohy se při dalším ostrém startu označí jako `failed` s pokynem k ruční kontrole. Automatické opakování publikace není záměrně zapnuté.

## Sběr leadů

Sběr zapněte až po kalibraci selektorů, nastavení konkrétních zdrojů a ověření právního důvodu zpracování:

```yaml
collection:
  enabled: true
  store_message_text: false
```

Spuštění pro všechny profily nebo pouze jeden:

```powershell
uv run marketplace-agent --config config.yaml collect --live
uv run marketplace-agent --config config.yaml collect --live --profile home
```

Knihovna `phonenumbers` validuje čísla podle nastaveného regionu, výchozí je CZ. Uložené číslo má formát E.164. Deduplikace používá profil, zdroj, konkrétní zdrojovou zprávu/komentář a číslo. Ve výchozím režimu se neukládá obsah zprávy, pouze číslo, odesílatel, zdrojová URL a čas zjištění.

Export do tabulky CSV:

```powershell
uv run marketplace-agent --config config.yaml export --output data\leads.csv
```

CSV i webhook jsou osobní data. Chraňte je přístupovými právy, šifrováním disku a pravidly pro retenci.

## POST JSON webhook

Konfigurace:

```yaml
webhook:
  enabled: true
  url: "https://your-endpoint.example/marketplace/leads"
  secret_env: WEBHOOK_SECRET
  timeout_seconds: 15
  max_attempts: 4
```

```powershell
$env:WEBHOOK_SECRET="v-produkci-pouzijte-spravce-tajemstvi"
uv run marketplace-agent --config config.yaml collect --live
```

Vlastnosti požadavku:

- metoda `POST`, UTF-8 JSON a `Content-Type: application/json`;
- událost `marketplace.leads`, verze schématu `1.0`, pole `leads`;
- stabilní ID události v `Idempotency-Key` a `X-Marketplace-Event`;
- `X-Marketplace-Signature-256` obsahuje `sha256=` a hex HMAC-SHA256 přesného těla požadavku, podepsaného `WEBHOOK_SECRET`;
- HTTP 429, 5xx a síťové chyby se opakují s exponenciálními odstupy;
- ostatní 4xx chyby se neopakují;
- nedoručené leady zůstávají v databázi pro další pokus.

Příklad těla:

```json
{
  "schema_version": "1.0",
  "event": "marketplace.leads",
  "event_id": "…",
  "generated_at": "2026-01-01T12:00:00+00:00",
  "leads": [{
    "id": 1,
    "profile_id": "home",
    "source": "messenger",
    "source_url": "https://www.facebook.com/messages/t/…",
    "source_key": "…",
    "phone": "+420777111222",
    "sender": "Ukázkový odesílatel",
    "message": null,
    "observed_at": "2026-01-01T11:59:00+00:00"
  }]
}
```

Příjemce musí ověřit HMAC porovnáním odolným vůči časovým únikům a před vložením řádků odstranit duplicity podle ID události. HTTPS je povinné; HTTP je povolené jen pro lokální vývoj na localhostu.

## AI oprava selektorů

AI je volitelná. Podporuje OpenAI-kompatibilní Responses API:

```yaml
agent:
  enabled: true
  base_url: https://api.openai.com/v1
  api_key_env: OPENAI_API_KEY
  model: gpt-4o-mini
  max_candidates: 3
```

```powershell
$env:OPENAI_API_KEY="..."
uv run marketplace-agent --config config.yaml run
```

Když selžou všechna YAML pravidla:

1. aplikace získá nejvýše 150 tlačítek a vstupních ovládacích prvků;
2. modelu pošle pouze tag a povolené atributy jako `role`, `aria-label`, `name`, `type`, `id`, `data-testid`, `placeholder` a `accept`;
3. hodnoty inputů a textareas se neposílají;
4. model vrátí strukturované CSS kandidáty podle JSON Schema;
5. kandidát se použije pouze tehdy, najde-li právě jeden dostupný prvek;
6. uloží se jako návrh do `selector_proposals`;
7. operátor jej posoudí a teprve potom ručně zapíše do YAML.

AI tím není „samoléčící se nekontrolovaný robot“. Jde o omezený návrh s ověřením a auditovatelnou stopou. Neodesílají se cookies, hesla, proxy přihlašovací údaje, celé HTML, seznamy telefonů ani těla zpráv.

Při chybě se lokálně uloží screenshot do `artifacts/`. Před jeho sdílením zkontrolujte, zda neobsahuje údaje o účtu nebo osobní data.

## Týdenní optimalizace a monitoring

```powershell
uv run marketplace-agent --config config.yaml report
uv run marketplace-agent --config config.yaml report --ai
```

`report` vypíše počty úloh a leadů podle stavů, nejčastější chyby a čekající návrhy selektorů. Režim `--ai` pošle agregované metriky modelu a vrátí doporučení pro spolehlivost a kvalitu obsahu. Před použitím tohoto režimu zkontrolujte, zda chybové texty neobsahují citlivá data ze stránky.

Pro týdenní přehled můžete `report --ai` naplánovat samostatně ve Windows Task Scheduleru. AI výstup je pouze doporučení; sám nemění rozvrh, texty, limity ani produkční YAML.

## Přehled příkazů

```text
validate [--content]       Kontrola YAML, profilů a volitelně Excelu/fotek
login --profile ID         Ruční přihlášení a uložení trvalé session
prepare-images             Odstranění metadat a převod fotografií
plan [--date YYYY-MM-DD]   Rozdělení unikátních inzerátů do časových slotů
run [--live] [--watch]     Suchý běh nebo ostré zpracování splatných úloh
collect --live             Sběr dobrovolně sdílených čísel a webhook
export --output PATH       Export všech evidovaných leadů do CSV
retry-job ID               Nové zařazení ručně ověřené neúspěšné úlohy
report [--ai]              Metriky, návrhy selektorů a volitelný AI přehled
```

Globální parametr `--config PATH` patří před název příkazu, jak ukazují příklady.

## Provozní doporučení

- Po aktualizaci Camoufoxu nebo změně selektorů proveďte kontrolu Excelu a jeden dohlížený suchý běh.
- `data/`, `profiles/` a `artifacts/` uložte na šifrovaný disk s omezenými ACL a nastavenou retencí.
- Zálohujte databázi a Excel; cookies zálohujte jen tehdy, pokud je skutečně potřebujete.
- Každý profil používejte se stabilním endpointem. Neměňte identity za účelem obcházení omezení.
- CAPTCHA, checkpoint nebo ověření je signál k zastavení, nikoliv k automatickému opakování.
- Ponechte konzervativní denní limity. Změna konfigurace nezpůsobí, že jinak zakázaná automatizace bude povolená.
- Kategorií vyžadující další povinná pole může být potřeba rozšířit malý publikační adaptér.

## Ověření a známé hranice

GitHub Actions instaluje balíček, spouští Ruff, striktní mypy, sestavení distribuce a validaci ukázkové konfigurace. Živý Facebook běh nelze poctivě potvrdit bez reálných účtů a kalibrace. Před produkcí proto proveďte:

1. ruční přihlášení všech profilů;
2. suchý běh a vizuální kontrolu formuláře na každém profilu;
3. schválení jednoho kontrolovaného ostrého inzerátu;
4. ověření podpisu a idempotence vašeho webhook příjemce;
5. kontrolu deduplikace leadů na vašich povolených zdrojích;
6. kontrolu kategorií, které mohou vyžadovat doplňující pole.

Proměnlivé jsou zejména Facebook A/B layouty, lokalizované popisky, expirace sessions, kvalita proxy a povinná pole konkrétní kategorie. Projekt neobsahuje řešení CAPTCHA, automatický warm-up, nábor skupin ani hromadné zprávy.

## Licence

MIT, viz `LICENSE`. Camoufox, Playwright a ostatní závislosti mají vlastní licence. Facebook a Meta jsou ochranné známky příslušných vlastníků; projekt není s Metou spojen.
