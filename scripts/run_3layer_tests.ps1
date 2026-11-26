[CmdletBinding()]
param(
    [ValidateSet('1', '2', '3', 'all')]
    [string]$Layer = 'all',
    [switch]$KeepInfra,
    [switch]$SkipExistingUnit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$be = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $be
$fe = Join-Path $repo 'FE'
$libs = Join-Path $be 'libs/common_auth'
$svcRoot = Join-Path $be 'services'
$suite = Join-Path $be 'tests_3layer'
$services = @('agent', 'consultation', 'contract', 'document', 'lawyer', 'location', 'notification', 'procedure', 'rag', 'user')
$failures = [Collections.Generic.List[object]]::new()
$initialEnv = @{}
$keepScratch = $false
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$scratch = Join-Path $tempRoot ("legal-3layer-" + [guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $scratch
$emptyEnv = Join-Path $scratch 'empty.env'
[IO.File]::WriteAllText($emptyEnv, '')
$pathSeparator = [IO.Path]::PathSeparator

function Set-RunEnv([string]$Name, [AllowNull()][string]$Value) {
    if (-not $initialEnv.ContainsKey($Name)) {
        $initialEnv[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
    }
    if ($Value -eq '') { $Value = $null }
    [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
}

function Add-Failure([string]$Name, [int]$ExitCode) {
    $failures.Add([pscustomobject]@{ Name = $Name; ExitCode = $ExitCode })
    Write-Host "[FAIL $ExitCode] $Name" -ForegroundColor Red
}

function Resolve-Tool([string[]]$Names) {
    foreach ($name in $Names) {
        $command = Get-Command $name -All -CommandType Application -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notmatch '[\\/]WindowsApps[\\/]' } |
            Select-Object -First 1
        if ($command) { return $command.Source }
    }
    throw "No executable found on PATH for $($Names -join ' or ')."
}

function Resolve-Python {
    $candidate = Resolve-Tool @('py', 'python')
    $realExe = & $candidate -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $realExe -and (Test-Path -LiteralPath $realExe.Trim())) {
        return $realExe.Trim()
    }
    return $candidate
}

function Invoke-Step([string]$Name, [string]$Program, [string[]]$Arguments) {
    Write-Host "`n--- $Name ---" -ForegroundColor Cyan
    & $Program @Arguments | Out-Host
    $code = $LASTEXITCODE
    if ($code -ne 0) { Add-Failure $Name $code }
    return $code
}

function Set-PythonPath([string]$Service = '') {
    $paths = @($libs, $be)
    if ($Service) { $paths = @((Join-Path $svcRoot "$Service-service")) + $paths }
    Set-RunEnv 'PYTHONPATH' ($paths -join $pathSeparator)
}

function Invoke-Pytest([string]$Name, [string[]]$Paths, [string]$Service = '',
                       [string]$WorkingDirectory = $scratch, [string[]]$Options = @()) {
    Set-PythonPath $Service
    $pytestTemp = Join-Path $scratch ("pytest-" + [guid]::NewGuid().ToString('N'))
    Push-Location $WorkingDirectory
    try {
        $baseOptions = @('-m', 'pytest', '-p', 'no:cacheprovider', '-ra', '--basetemp', $pytestTemp)
        $null = Invoke-Step $Name $python ($baseOptions + $Options + $Paths)
    } finally {
        Pop-Location
    }
}

function Invoke-Layer1 {
    if (-not $SkipExistingUnit) {
        Invoke-Pytest 'common_auth unit tests' @((Join-Path $libs 'tests'))
        foreach ($service in $services) {
            $directory = Join-Path $svcRoot "$service-service/tests"
            if ($service -eq 'location' -and -not (Get-ChildItem $directory -File -Filter 'test_*.py')) {
                continue # The location suite lives in BE/tests in this checkout.
            }
            $svcOpts = @()
            if ($service -eq 'contract') {
                # ReferenceCatalogTests requires a seeded Supabase catalog DB; SystemPdfSourceTests uses Linux fonts and runs in Layer 3 container.
                $svcOpts = @(
                    '--ignore', (Join-Path $directory 'test_reference_catalog.py'),
                    '--ignore', (Join-Path $directory 'test_system_pdf_source.py')
                )
            }
            Invoke-Pytest "$service-service unit tests" @($directory) $service $scratch $svcOpts
        }

        $procedureFiles = @('test_optional_repeat_export.py', 'test_profile_binding.py', 'test_word_editor.py')
        $rootTests = @(Get-ChildItem (Join-Path $be 'tests') -File -Filter 'test_*.py')
        $locationTests = @($rootTests | Where-Object Name -like 'test_location_*.py')
        Invoke-Pytest 'Location repository tests' @($locationTests.FullName) 'location'
        foreach ($file in $procedureFiles) {
            Invoke-Pytest "Procedure repository test $file" @((Join-Path $be "tests/$file")) 'procedure' `
                (Join-Path $svcRoot 'procedure-service')
        }
        Invoke-Pytest 'Document standalone identity tests' `
            @((Join-Path $be 'tests/test_identity_semantic_labels_standalone.py')) 'document'
        $repoContractTests = @(
            (Join-Path $be 'tests/test_gateway_config.py'),
            (Join-Path $be 'tests/test_render_blueprint.py'),
            (Join-Path $be 'tests/test_openapi.py')
        )
        Invoke-Pytest 'Backend repository contract & blueprint tests' $repoContractTests

        $npm = Resolve-Tool @('npm.cmd', 'npm')
        $node = Resolve-Tool 'node'
        Push-Location $fe
        try {
            $null = Invoke-Step 'Frontend unit tests' $npm @('test')
        } finally {
            Pop-Location
        }
        $null = Invoke-Step 'OnlyOffice bootstrap unit tests' $node `
            @('--test', (Join-Path $be 'tests/onlyoffice-bootstrap.test.cjs'))
        foreach ($verifier in @(Get-ChildItem (Join-Path $repo '.agents/scripts') -File -Filter 'verify_*.py')) {
            $null = Invoke-Step "Harness verifier $($verifier.BaseName)" $python @($verifier.FullName, '--all')
        }
    }
    Invoke-Pytest 'Layer 1 adversarial tests' @((Join-Path $suite 'layer1_adversarial')) '' $scratch @('-v')
}

function Invoke-LegacyLayer2 {
    $strict = @('-p', 'tests_3layer.no_skips')
    $db = 'postgresql://postgres:postgres@127.0.0.1:55432'
    Set-RunEnv 'RABBITMQ_TEST_URL' 'amqp://guest:guest@127.0.0.1:55672/'
    Set-RunEnv 'RABBITMQ_URL' 'amqp://guest:guest@127.0.0.1:55672/'
    Set-RunEnv 'RABBITMQ_ENABLED' 'true'
    Set-RunEnv 'TEST_POSTGRES_URL' "$db/consultation_db"
    Set-RunEnv 'AUTH_TEST_POSTGRES_URL' "$db/auth_refresh_test"
    Set-RunEnv 'RAG_TEST_DATABASE_URL' "$db/rag_legacy_test"
    Set-RunEnv 'DOCUMENT_TEST_DATABASE_URL' "$db/document_schema_test"
    Invoke-Pytest 'Existing common_auth RabbitMQ integration' @(
        (Join-Path $libs 'tests/test_outbox.py'), (Join-Path $libs 'tests/test_revocation_events.py')
    ) '' $scratch $strict
    Invoke-Pytest 'Existing User Postgres and RabbitMQ integrations' @(
        (Join-Path $svcRoot 'user-service/tests/test_refresh_postgres.py'),
        (Join-Path $svcRoot 'user-service/tests/test_logout_rabbitmq.py')
    ) 'user' $scratch $strict
    Invoke-Pytest 'Existing Consultation Postgres integrations' @(
        (Join-Path $svcRoot 'consultation-service/tests/test_concurrency_postgres.py'),
        (Join-Path $svcRoot 'consultation-service/tests/test_summary_uniqueness.py')
    ) 'consultation' $scratch ($strict + @('-k', 'not (test_sql_migration_nang_cap_constraint_cu_va_chay_lai_duoc and sqlite)'))
    Invoke-Pytest 'Existing RAG pgvector integration' @(
        (Join-Path $svcRoot 'rag-service/tests/test_pgvector_integration.py')
    ) 'rag' $scratch ($strict + @('-p', 'tests_3layer.legacy_rag_schema'))
    Invoke-Pytest 'Existing Document migration and RLS integration' @(
        (Join-Path $svcRoot 'document-service/tests/test_database_schema.py')
    ) 'document' $scratch $strict

    Set-PythonPath
    if ((Invoke-Step 'Reset disposable legacy Document database' $python `
        @('-m', 'tests_3layer.layer2_cross_service.db_bootstrap', '--reset-legacy-document')) -eq 0) {
        $docFiles = @('upload', 'download_delete', 'handoff', 'identity_ocr', 'identity_profile',
                      'internal_content', 'local_ocr', 'outbox', 'worker')
        Invoke-Pytest 'Existing Document API/repository integrations' @(
            $docFiles | ForEach-Object { Join-Path $be "tests/test_document_$_.py" }
        ) 'document' $scratch ($strict + @('-k', 'not openapi and not test_real_http_contract_and_admin_is_not_owner and not test_binary_headers_and_204_body and not test_pdf_blank_valid'))
    }

    try {
        # The legacy suite recognizes this exact URI. libpq supplies the
        # dedicated test port/password because the URI omits both.
        Set-RunEnv 'TEST_REFERENCE_DATABASE_URL' 'postgresql://postgres@127.0.0.1/reference_upload_tests'
        Set-RunEnv 'PGPORT' '55432'
        Set-RunEnv 'PGPASSWORD' 'postgres'
        Invoke-Pytest 'Existing Contract reference upload integration' @(
            (Join-Path $svcRoot 'contract-service/tests/test_reference_uploads.py')
        ) 'contract' $scratch $strict
    } finally {
        Set-RunEnv 'TEST_REFERENCE_DATABASE_URL' $null
        Set-RunEnv 'PGPORT' $null
        Set-RunEnv 'PGPASSWORD' $null
    }

    Set-PythonPath
    foreach ($test in (Get-ChildItem (Join-Path $be 'tests_rabbitmq_reality_check') -File -Filter 'test_*.py' | Sort-Object Name)) {
        $null = Invoke-Step "Existing RabbitMQ check $($test.BaseName)" $python `
            @('-m', 'tests_3layer.run_rabbit_legacy', $test.Name)
    }
}

function Invoke-DockerLayer([string]$Number) {
    $isE2E = $Number -eq '3'
    $name = if ($isE2E) { 'legal-test-e2e' } else { 'legal-test-infra' }
    $file = if ($isE2E) { 'docker-compose.test-e2e.yml' } else { 'docker-compose.test-infra.yml' }
    $compose = @('compose', '--env-file', $emptyEnv, '-p', $name, '-f', (Join-Path $be $file))
    $hasOwnership = $false
    $failureCount = $failures.Count
    Push-Location $be
    try {
        Set-PythonPath
        if ($isE2E) {
            Set-RunEnv 'LEGAL_TEST_KEYS_DIR' (Join-Path $scratch 'jwt')
            if ((Invoke-Step 'Generate temporary RSA test keys' $python @('-m', 'tests_3layer.support')) -ne 0) { return }
        }
        if ((Invoke-Step "Validate $name Compose" $docker ($compose + @('config', '-q'))) -ne 0) { return }
        $existing = @(& $docker @compose ps -a -q)
        $psCode = $LASTEXITCODE
        if ($psCode -ne 0) {
            Add-Failure "List $name containers" $psCode
            return
        }
        if ($existing.Count -gt 0) {
            throw "Project $name already has containers. Remove the retained test stack before rerunning."
        }
        $hasOwnership = $true
        $common = @('up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '120')
        $start = if ($isE2E) { $common + @('postgres-e2e', 'redis', 'rabbitmq-core-events') } else { $common }
        if ((Invoke-Step "Start $name dependencies" $docker ($compose + $start)) -ne 0) { return }

        $bootstrap = @('-m', 'tests_3layer.layer2_cross_service.db_bootstrap')
        if ($isE2E) { $bootstrap += '--e2e' }
        if ((Invoke-Step "Apply PostgreSQL migrations for $name" $python $bootstrap) -ne 0) { return }

        if ($isE2E) {
            if ((Invoke-Step 'Start and await all Linux services and workers' $docker ($compose + $common)) -ne 0) { return }
            Set-RunEnv 'TEST_LAYER3_ENABLED' '1'
            Invoke-Pytest 'Layer 3 Gateway and Docker E2E' @((Join-Path $suite 'layer3_docker_e2e')) '' $scratch `
                @('-v', '-p', 'tests_3layer.no_skips')
            Set-RunEnv 'BACKEND_TEST_URL' 'http://127.0.0.1:18080'
            $token = & $python -m tests_3layer.legacy_e2e_token
            $tokenCode = $LASTEXITCODE
            if ($tokenCode -ne 0) {
                Add-Failure 'Mint JWT for existing Gateway tests' $tokenCode
            } else {
                Set-RunEnv 'BACKEND_TEST_TOKEN' $token
                Invoke-Pytest 'Existing Gateway HTTP integration' @((Join-Path $be 'tests/test_docker_http.py')) '' $scratch `
                    @('-p', 'tests_3layer.no_skips')
            }
        } else {
            Set-RunEnv 'TEST_LAYER2_ENABLED' '1'
            Invoke-Pytest 'Layer 2 actual Postgres, Redis and RabbitMQ' @((Join-Path $suite 'layer2_cross_service')) '' $scratch `
                @('-v', '-p', 'tests_3layer.no_skips')
            Push-Location $scratch
            try {
                Invoke-LegacyLayer2
            } finally {
                foreach ($variable in @('TEST_LAYER2_ENABLED', 'RABBITMQ_TEST_URL', 'RABBITMQ_URL',
                       'TEST_POSTGRES_URL', 'AUTH_TEST_POSTGRES_URL', 'RAG_TEST_DATABASE_URL',
                       'DOCUMENT_TEST_DATABASE_URL')) {
                    Set-RunEnv $variable $null
                }
                Set-RunEnv 'RABBITMQ_ENABLED' 'false'
                Pop-Location
            }
        }
    } catch {
        Add-Failure "Layer $Number orchestration: $($_.Exception.Message)" 1
    } finally {
        try {
            if ($hasOwnership) {
                try {
                    if ($failures.Count -gt $failureCount) {
                        $null = Invoke-Step "Show $name container logs" $docker ($compose + @('logs', '--tail=80'))
                    }
                } finally {
                    if ($KeepInfra) {
                        $script:keepScratch = $true
                        Write-Host "Retained project $name. Compose env and test keys: $scratch"
                    } else {
                        $null = Invoke-Step "Remove $name test stack" $docker ($compose + @('down', '-v', '--remove-orphans'))
                    }
                }
            }
        } finally {
            Pop-Location
        }
    }
}

try {
    foreach ($entry in Get-ChildItem Env:) {
        if ($entry.Name -match '^(PG|COMPOSE_|PYTEST_|OPENROUTER_|OPENAI_|GEMINI_|GOOGLE_|GROQ_|EKYC_|FIREBASE_|RABBITMQ_|REDIS_|JWT_|SUPABASE_|TEST_LAYER|BACKEND_TEST_|DOCKER_HOST$)|(_DATABASE_URL|_SERVICE_URL|_SUPABASE_.*|_API_KEY|_TEST_URL|_TEST_DATABASE_URL|_TEST_POSTGRES_URL)$|^(DATABASE_URL|INTERNAL_TOKEN|LAWYER_SERVICE_TOKEN|PROCEDURE_WRITE_MODE|TEST_POSTGRES_URL|TEST_REFERENCE_DATABASE_URL|LLM_BYPASS|RELEVANCE_CHECK|ALL_PROXY|HTTPS?_PROXY)$') {
            Set-RunEnv $entry.Name $null
        }
    }
    Set-RunEnv 'LEGAL_TEST_COMPOSE_ENV_FILE' $emptyEnv
    Set-RunEnv 'PYTHON_DOTENV_DISABLED' '1'
    Set-RunEnv 'PROCEDURE_SKIP_DOTENV' '1'
    Set-RunEnv 'PYTHONDONTWRITEBYTECODE' '1'
    Set-RunEnv 'PYTHONUTF8' '1'
    Set-RunEnv 'PYTHONPYCACHEPREFIX' (Join-Path $scratch 'compiled')
    Set-RunEnv 'RABBITMQ_ENABLED' 'false'
    Set-RunEnv 'HYPOTHESIS_STORAGE_DIRECTORY' (Join-Path $scratch '.hypothesis')

    $python = Resolve-Python
    $syntaxCode = Invoke-Step 'Compile new Python tests' $python @('-m', 'compileall', '-q', $suite)
    if ($syntaxCode -ne 0) { exit $syntaxCode }
    if ($Layer -in @('1', 'all')) {
        try { Invoke-Layer1 } catch { Add-Failure "Layer 1 orchestration: $($_.Exception.Message)" 1 }
    }
    if ($Layer -in @('2', '3', 'all')) {
        $docker = Resolve-Tool 'docker'
        $hostEndpoint = & $docker context inspect --format '{{.Endpoints.docker.Host}}'
        $contextCode = $LASTEXITCODE
        if ($contextCode -ne 0) {
            Add-Failure 'Inspect Docker context' $contextCode
        } elseif ($hostEndpoint -notmatch '^(npipe:////\./pipe/|unix:///)') {
            throw 'The isolated test projects require a local Docker engine.'
        } else {
            if ($Layer -in @('2', 'all')) { Invoke-DockerLayer '2' }
            if ($Layer -in @('3', 'all')) { Invoke-DockerLayer '3' }
        }
    }
} catch {
    Add-Failure $_.Exception.Message 1
} finally {
    foreach ($entry in $initialEnv.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, $entry.Value, 'Process')
    }
    if (-not $keepScratch) {
        try {
            $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
            if ([IO.Path]::GetDirectoryName($resolvedScratch).TrimEnd('\', '/') -ne $tempRoot.TrimEnd('\', '/') -or
                    [IO.Path]::GetFileName($resolvedScratch) -notmatch '^legal-3layer-[a-f0-9]{32}$') {
                throw "Unexpected test scratch path: $resolvedScratch"
            }
            Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
        } catch {
            Add-Failure "Clean temporary test files: $($_.Exception.Message)" 1
        }
    }
}

if ($failures.Count) {
    $failures | Format-Table -AutoSize | Out-Host
    exit $failures[0].ExitCode
}
Write-Host 'Selected test layers completed successfully.' -ForegroundColor Green
exit 0
