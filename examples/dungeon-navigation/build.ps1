[CmdletBinding()]
param(
    [string] $Game = (Join-Path $PSScriptRoot '../../../kawaii-power-little-adventure'),
    [string] $Unity = 'C:/Program Files/Unity/Hub/Editor/6000.3.4f1/Editor/Unity.exe',
    [switch] $Training
)

$ErrorActionPreference = 'Stop'
$gameRoot = (Resolve-Path -LiteralPath $Game).Path
$framework = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$sample = Join-Path $framework 'examples/actor-control'
$assets = Join-Path $PSScriptRoot 'Assets'
$build = Join-Path $PSScriptRoot 'Build'
$gameRevision = '2d4336881f9de9ed5bf4b8d182378a36ed805452'
$cathedralRevision = '4d97815fff9509e4c540a30066846d2f2af608fa'
$cavesRevision = 'c17f1cbe47d290a03786a6a66cdd084e020fe7ec'
if (!(Test-Path -LiteralPath $Unity -PathType Leaf)) { throw "Missing Unity Editor: $Unity" }

function Expand-GameSources([string] $revision, [string[]] $paths) {
    $archive = Join-Path $build "$revision.tar"
    git -c "safe.directory=$($gameRoot.Replace('\','/'))" -C $gameRoot archive "--output=$archive" $revision -- $paths
    if ($LASTEXITCODE -ne 0) { throw "Could not read game sources at $revision." }
    tar.exe -xf $archive -C $PSScriptRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not unpack game sources at $revision." }
    Remove-Item -LiteralPath $archive
}

# Read pinned game sources without switching or modifying the game's checkout.
New-Item -ItemType Directory -Force -Path $build, "$assets/BuildSupport/Editor", "$assets/Settings" | Out-Null
Copy-Item -LiteralPath "$sample/ProjectSettings" -Destination $PSScriptRoot -Recurse -Force
Expand-GameSources $gameRevision @(
    'Assets/PCGDungeon', 'Assets/PCGDungeon.meta', 'Assets/Settings',
    'ProjectSettings/TagManager.asset', 'ProjectSettings/TimeManager.asset', 'ProjectSettings/DynamicsManager.asset',
    'ProjectSettings/GraphicsSettings.asset', 'ProjectSettings/QualitySettings.asset'
)
Expand-GameSources $cathedralRevision @(
    'Assets/PCGDungeon/Prefabs/Structure/Cathedral*',
    'Assets/PCGDungeon/Scripts/Data/CathedralSettings.cs*',
    'Assets/PCGDungeon/Scripts/Data/GenerationSettings.cs',
    'Assets/PCGDungeon/Scripts/Generation/Cathedral*.cs*',
    'Assets/PCGDungeon/Scripts/Generation/*Predungeon*.cs*',
    'Assets/PCGDungeon/Scripts/Generation/RoomsAndCorridorsGenerator.cs*',
    'Assets/PCGDungeon/Scripts/Generation/Generator.cs',
    'Assets/PCGDungeon/Scripts/Generation/PcgGenerationParameters.cs',
    'Assets/PCGDungeon/Scripts/Generation/Room.cs',
    'Assets/PCGDungeon/Settings/Cathedral Settings.asset*',
    'Assets/PCGDungeon/Settings/Settings.asset'
)
Expand-GameSources $cavesRevision @(
    'Assets/PCGDungeon/Materials/Caves*',
    'Assets/PCGDungeon/Scripts/Gameplay/CaveVisuals.cs*',
    'Assets/PCGDungeon/Scripts/Generation/CaveLayout.cs*',
    'Assets/PCGDungeon/Scripts/Generation/Generator.Caves.cs*',
    'Assets/PCGDungeon/Settings/CavesSettings.asset*'
)
# Combine the two generation entry points in the example's imported game sources.
$examplePath = 'examples/dungeon-navigation'
git -c "safe.directory=$($framework.Replace('\','/'))" -C $framework apply --no-index --ignore-space-change "--directory=$examplePath" -- "$examplePath/fixtures/caves-generation.patch"
if ($LASTEXITCODE -ne 0) { throw 'Could not integrate the pinned Caves generator.' }
tar.exe -xf "$PSScriptRoot/fixtures/qa85-navigation-sources.zip" -C $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the historical navigation sources.' }
$navigation = 'Assets/PCGDungeon/Scripts/QA'
foreach ($file in 'ModelSensorGrid.cs', 'ModelSensorPackage.cs', 'QaNavigator4.cs', 'QaWaypointMapSearch.cs', 'QaWaypointClearance.cs', 'QaPlayerController.cs') {
    Copy-Item -LiteralPath "$gameRoot/$navigation/$file" -Destination "$PSScriptRoot/$navigation/$file" -Force
}
Copy-Item -Path "$sample/Assets/Settings/AppUISettings.asset*" -Destination "$assets/Settings" -Force
Copy-Item -Path "$sample/Assets/Editor/CpuSampleShaders.cs*" -Destination "$assets/BuildSupport/Editor" -Force

$method = 'BuildDungeonTrials.Build'
$log = Join-Path $build 'unity-build.log'
if ($Training) {
    $method = 'BuildDungeonTrials.BuildTraining'
    $log = Join-Path $build 'unity-training-build.log'
}
# Unity's render pipeline importers still use Windows paths limited to 260 characters.
$project = $PSScriptRoot
$drive = $null
if ($project.Length -gt 90) {
    $sourceRoot = Split-Path $framework -Parent
    $drives = [IO.Directory]::GetLogicalDrives()
    $drive = 90..68 | ForEach-Object { ([char]$_).ToString() + ':' } |
        Where-Object { "$_\" -notin $drives } | Select-Object -First 1
    if (!$drive) { throw 'Unity needs an available drive letter to shorten this project path.' }
    subst.exe $drive $sourceRoot
    if ($LASTEXITCODE -ne 0) { throw "Could not map the Unity build directory to $drive" }
    $project = Join-Path "$drive\" $PSScriptRoot.Substring($sourceRoot.Length + 1)
}
try {
    $arguments = "-batchmode -nographics -quit -projectPath `"$project`" -buildTarget Linux64 -executeMethod $method -logFile `"$log`""
    $editor = Start-Process -FilePath $Unity -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
    if ($editor.ExitCode -ne 0) { throw "Unity build failed ($($editor.ExitCode)). See $log" }
}
finally {
    if ($drive) { subst.exe $drive /D }
}
if ($Training) {
    $rl = Join-Path (Split-Path $framework -Parent) 'unity-reinforcement-learning/PythonEnv'
    docker build -f "$PSScriptRoot/Dockerfile.training" --build-context "unity-rl=$rl" -t qa-cathedral-training:local $framework
} else {
    docker build -f "$PSScriptRoot/Dockerfile" -t qa-dungeon-trials:local $framework
}
if ($LASTEXITCODE -ne 0) { throw 'Dungeon image build failed.' }
